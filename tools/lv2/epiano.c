/* Headless host for the Fedora MDA ePiano Atom/MIDI port layout. */
#define _POSIX_C_SOURCE 200809L
#include <jack/jack.h>
#include <jack/ringbuffer.h>
#include <lilv/lilv.h>
#include <lv2/atom/util.h>
#include <lv2/midi/midi.h>
#include <lv2/urid/urid.h>
#include <sndfile.h>
#include <math.h>
#include <poll.h>
#include <stdatomic.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#define URI "http://drobilla.net/plugins/mda/EPiano"
#define CONTROLS 12
#define CAPACITY 2048
#define BLOCK 256

_Static_assert(ATOMIC_BOOL_LOCK_FREE == 2 && ATOMIC_INT_LOCK_FREE == 2,
               "JACK callback requires lock-free counters and flags");

typedef struct {
    uint64_t at;
    unsigned code;
    float value;
    char kind;
} Event;

typedef struct {
    LilvWorld *world;
    LilvInstance *plugin;
    float controls[CONTROLS];
    union {
        uint64_t alignment;
        uint8_t bytes[sizeof(LV2_Atom_Sequence) + CAPACITY * 24];
    } buffer;
    jack_client_t *jack;
    jack_port_t *left, *right;
    jack_ringbuffer_t *queue;
    atomic_bool stopping, released, lost, started;
    atomic_uint late, xruns, startup_xruns;
    unsigned rate;
    int64_t jack_offset;
} Host;

static const char *symbols[] = {
    "env_decay", "env_release", "hardness", "treble_boost", "modulation",
    "lfo_rate", "vel_sense", "stereo_width", "polyphonic", "fine_tuning",
    "random_tuning", "overdrive", "left_out", "right_out", "event_in"
};

static LV2_URID map_uri(LV2_URID_Map_Handle handle, const char *uri) {
    (void)handle;
    if (!strcmp(uri, LV2_ATOM__Sequence)) return 1;
    if (!strcmp(uri, LV2_MIDI__MidiEvent)) return 2;
    return 0;
}

static LV2_URID_Map mapper = {NULL, map_uri};
static LV2_Feature map_feature = {LV2_URID__map, &mapper};
static const LV2_Feature *features[] = {&map_feature, NULL};

static bool node_equals(const LilvNode *node, const char *uri) {
    return node && lilv_node_is_uri(node) && !strcmp(lilv_node_as_uri(node), uri);
}

static int version(const LilvPlugin *plugin, LilvWorld *world, const char *uri) {
    LilvNode *predicate = lilv_new_uri(world, uri);
    LilvNodes *values = lilv_plugin_get_value(plugin, predicate);
    const LilvNode *value = values ? lilv_nodes_get_first(values) : NULL;
    int result = value && lilv_node_is_int(value) ? lilv_node_as_int(value) : -1;
    lilv_nodes_free(values);
    lilv_node_free(predicate);
    return result;
}

static const LilvPlugin *discover(Host *host, bool probe) {
    host->world = lilv_world_new();
    if (!host->world) return NULL;
    lilv_world_load_all(host->world);
    LilvNode *uri = lilv_new_uri(host->world, URI);
    const LilvPlugin *plugin = lilv_plugins_get_by_uri(lilv_world_get_all_plugins(host->world), uri);
    lilv_node_free(uri);
    if (!plugin) {
        fprintf(stderr, "MDA ePiano missing. Install lv2-mdala-plugins or set LV2_PATH.\n");
        return NULL;
    }
    if (lilv_plugin_get_num_ports(plugin) != 15 ||
        version(plugin, host->world, LV2_CORE__minorVersion) != 2 ||
        version(plugin, host->world, LV2_CORE__microVersion) != 0) return NULL;

    LilvNodes *required = lilv_plugin_get_required_features(plugin);
    bool supported = true;
    LILV_FOREACH(nodes, iter, required) {
        if (!node_equals(lilv_nodes_get(required, iter), LV2_URID__map)) supported = false;
    }
    lilv_nodes_free(required);
    if (!supported) return NULL;

    float lo[15], hi[15], defaults[15];
    lilv_plugin_get_port_ranges_float(plugin, lo, hi, defaults);
    for (unsigned i = 0; i < 15; ++i) {
        const LilvPort *port = lilv_plugin_get_port_by_index(plugin, i);
        const LilvNode *symbol = lilv_port_get_symbol(plugin, port);
        const char *type = i < CONTROLS ? LV2_CORE__ControlPort :
                           i < 14 ? LV2_CORE__AudioPort : LV2_ATOM__AtomPort;
        LilvNode *class = lilv_new_uri(host->world, type);
        LilvNode *direction = lilv_new_uri(host->world,
            i == 12 || i == 13 ? LV2_CORE__OutputPort : LV2_CORE__InputPort);
        bool matches = symbol && !strcmp(lilv_node_as_string(symbol), symbols[i])
            && lilv_port_is_a(plugin, port, class) && lilv_port_is_a(plugin, port, direction);
        lilv_node_free(class);
        lilv_node_free(direction);
        if (!matches) return NULL;
        if (i < CONTROLS) {
            LilvNode *toggled = lilv_new_uri(host->world, LV2_CORE__toggled);
            bool is_toggle = lilv_port_has_property(plugin, port, toggled);
            lilv_node_free(toggled);
            if (lo[i] != 0.f || hi[i] != 1.f || !isfinite(defaults[i]) || is_toggle != (i == 8))
                return NULL;
            host->controls[i] = defaults[i];
        }
        if (i == 14) {
            LilvNode *midi = lilv_new_uri(host->world, LV2_MIDI__MidiEvent);
            LilvNode *buffer_type = lilv_new_uri(host->world, LV2_ATOM__bufferType);
            LilvNodes *types = lilv_port_get_value(plugin, port, buffer_type);
            matches = lilv_port_supports_event(plugin, port, midi) &&
                types && lilv_nodes_size(types) == 1 &&
                node_equals(lilv_nodes_get_first(types), LV2_ATOM__Sequence);
            lilv_nodes_free(types);
            lilv_node_free(buffer_type);
            lilv_node_free(midi);
            if (!matches) return NULL;
        }
    }
    if (probe) {
        printf("%s 2.0\n", URI);
        for (unsigned i = 0; i < CONTROLS; ++i)
            printf("%s %.9g %.9g %.9g\n", symbols[i], lo[i], hi[i], defaults[i]);
    }
    return plugin;
}

static LV2_Atom_Sequence *sequence(Host *host) {
    return (LV2_Atom_Sequence *)host->buffer.bytes;
}

static void clear_sequence(Host *host) {
    LV2_Atom_Sequence *seq = sequence(host);
    seq->atom.type = 1;
    seq->body.unit = 0;
    seq->body.pad = 0;
    lv2_atom_sequence_clear(seq);
}

static void midi(Host *host, unsigned frame, unsigned code, unsigned velocity) {
    struct {
        LV2_Atom_Event event;
        uint8_t data[8];
    } message = {{.time.frames = frame, .body = {.size = 3, .type = 2}},
                 {code < 128 ? 0x90 : 0x80, code % 128, velocity}};
    lv2_atom_sequence_append_event(sequence(host),
        sizeof(host->buffer.bytes) - sizeof(LV2_Atom), &message.event);
}

static void run_slice(Host *host, float *left, float *right, unsigned offset, unsigned frames) {
    lilv_instance_connect_port(host->plugin, 12, left + offset);
    lilv_instance_connect_port(host->plugin, 13, right + offset);
    lilv_instance_run(host->plugin, frames);
}

typedef struct {
    Host *host;
    const Event *events;
    size_t count, index;
    bool live;
} Source;

static bool peek(Source *source, Event *event) {
    if (source->live)
        return jack_ringbuffer_peek(source->host->queue, (char *)event, sizeof(*event)) == sizeof(*event);
    if (source->index == source->count) return false;
    *event = source->events[source->index];
    return true;
}

static void consume(Source *source) {
    if (source->live) jack_ringbuffer_read_advance(source->host->queue, sizeof(Event));
    else ++source->index;
}

static void process(Source *source, uint64_t start, unsigned frames, float *left, float *right) {
    Host *host = source->host;
    unsigned offset = 0;
    Event event;
    clear_sequence(host);
    /* At most one ring's worth of work, even if the producer refills it. */
    for (unsigned i = 0; i < CAPACITY && peek(source, &event); ++i) {
        uint64_t frame = event.at > start ? event.at - start : 0;
        if (source->live) {
            if (frame > 1000000) break;
            frame = frame * host->rate / 1000000;
        }
        if (frame >= frames) break;
        consume(source);
        if (source->live && event.at < start) atomic_fetch_add(&host->late, 1);
        if (event.kind == 'p') {
            if (frame > offset) {
                run_slice(host, left, right, offset, (unsigned)frame - offset);
                offset = (unsigned)frame;
                clear_sequence(host);
            }
            host->controls[event.code] = event.value;
        } else {
            if (source->live && event.code < 128)
                atomic_store(&host->started, true);
            midi(host, (unsigned)frame - offset, event.code, (unsigned)event.value);
        }
    }
    run_slice(host, left, right, offset, frames - offset);
    /* Use the same output gain in both render modes. */
    for (unsigned i = 0; i < frames; ++i) {
        left[i] *= .22f;
        right[i] *= .22f;
    }
}

static int jack_process(jack_nframes_t frames, void *data) {
    Host *host = data;
    float *left = jack_port_get_buffer(host->left, frames);
    float *right = jack_port_get_buffer(host->right, frames);
    if (atomic_load(&host->stopping)) {
        clear_sequence(host);
        if (!atomic_load(&host->released)) {
            for (unsigned note = 0; note < 128; ++note) midi(host, 0, 128 + note, 0);
            atomic_store(&host->released, true);
        }
        run_slice(host, left, right, 0, frames);
        for (unsigned i = 0; i < frames; ++i) {
            left[i] *= .22f;
            right[i] *= .22f;
        }
        return 0;
    }
    jack_nframes_t cycle_frame;
    jack_time_t cycle_time, next;
    float period;
    if (jack_get_cycle_times(host->jack, &cycle_frame, &cycle_time, &next, &period))
        cycle_time = jack_frames_to_time(host->jack, jack_last_frame_time(host->jack));
    Source source = {.host = host, .live = true};
    process(&source, (uint64_t)((int64_t)cycle_time - host->jack_offset), frames, left, right);
    return 0;
}

static void jack_lost(void *data) {
    atomic_store(&((Host *)data)->lost, true);
}

static int jack_xrun(void *data) {
    Host *host = data;
    if (atomic_load(&host->started)) atomic_fetch_add(&host->xruns, 1);
    else atomic_fetch_add(&host->startup_xruns, 1);
    return 0;
}

static int jack_rate(jack_nframes_t rate, void *data) {
    Host *host = data;
    if (rate != host->rate) atomic_store(&host->lost, true);
    return 0;
}

static bool read_events(Event *events, unsigned count, uint64_t *previous) {
    for (unsigned i = 0; i < count; ++i) {
        char line[128], extra;
        unsigned long long at;
        Event *event = &events[i];
        if (!fgets(line, sizeof(line), stdin) ||
            sscanf(line, " %c %llu %u %f %c", &event->kind, &at, &event->code, &event->value, &extra) != 4
            || at < *previous || !isfinite(event->value)) return false;
        if (event->kind == 'p') {
            if (event->code >= CONTROLS || event->value < 0 || event->value > 1) return false;
        } else if (event->kind != 'm' || event->code > 255 || event->value < 0 || event->value > 127) {
            return false;
        }
        *previous = event->at = at;
    }
    return true;
}

static int live(Host *host) {
    jack_status_t status;
    host->jack = jack_client_open("DINAD-EPiano", JackNoStartServer | JackUseExactName, &status);
    if (!host->jack) return 1;
    if (jack_get_sample_rate(host->jack) != host->rate) {
        fprintf(stderr, "JACK sample rate does not match configured sample_rate.\n");
        return 1;
    }
    host->queue = jack_ringbuffer_create(CAPACITY * sizeof(Event));
    host->left = jack_port_register(host->jack, "left", JACK_DEFAULT_AUDIO_TYPE, JackPortIsOutput, 0);
    host->right = jack_port_register(host->jack, "right", JACK_DEFAULT_AUDIO_TYPE, JackPortIsOutput, 0);
    if (!host->queue || !host->left || !host->right) return 1;
    struct timespec now;
    clock_gettime(CLOCK_MONOTONIC, &now);
    host->jack_offset = (int64_t)jack_get_time() - ((int64_t)now.tv_sec * 1000000 + now.tv_nsec / 1000);
    jack_on_shutdown(host->jack, jack_lost, host);
    if (jack_set_process_callback(host->jack, jack_process, host) ||
        jack_set_xrun_callback(host->jack, jack_xrun, host) ||
        jack_set_sample_rate_callback(host->jack, jack_rate, host) ||
        jack_activate(host->jack)) return 1;

    // Give PipeWire a short window to register and connect the audio ports
    // before DINAD commits the first note deadline.
    struct timespec startup = {.tv_nsec = 150000000};
    nanosleep(&startup, NULL);
    if (atomic_load(&host->lost)) return 1;
    puts("READY");
    fflush(stdout);
    setvbuf(stdin, NULL, _IONBF, 0);
    uint64_t previous = 0;
    int result = 0;
    while (!atomic_load(&host->lost)) {
        struct pollfd fd = {.fd = 0, .events = POLLIN};
        int ready = poll(&fd, 1, 100);
        if (ready < 0) { result = 1; break; }
        if (!ready) continue;
        char line[64], extra;
        unsigned count;
        if (!fgets(line, sizeof(line), stdin) || !strcmp(line, "STOP\n")) break;
        Event batch[CAPACITY];
        if (sscanf(line, "%u %c", &count, &extra) != 1 || count >= CAPACITY ||
            count * sizeof(Event) > jack_ringbuffer_write_space(host->queue) ||
            !read_events(batch, count, &previous)) {
            fprintf(stderr, "Invalid or overflowing live event batch.\n");
            result = 1;
            break;
        }
        jack_ringbuffer_write(host->queue, (char *)batch, count * sizeof(Event));
        puts("OK");
        fflush(stdout);
    }
    atomic_store(&host->stopping, true);
    struct timespec release = {.tv_nsec = 500000000};
    if (!atomic_load(&host->lost)) nanosleep(&release, NULL);
    fprintf(stderr, "EPiano live counters: %u late events, %u playback xruns, %u startup xruns\n",
            atomic_load(&host->late), atomic_load(&host->xruns), atomic_load(&host->startup_xruns));
    return result || atomic_load(&host->lost) || !atomic_load(&host->released);
}

static int render(Host *host, const char *path, uint64_t frames) {
    unsigned count;
    if (scanf("%u", &count) != 1 || count > 200000 || getchar() != '\n') return 1;
    Event *events = calloc(count ? count : 1, sizeof(Event));
    uint64_t last = 0;
    if (!events || !read_events(events, count, &last) || (count && last >= frames)) {
        free(events);
        return 1;
    }
    /* Validate density before creating output. */
    unsigned first = 0;
    for (unsigned i = 0; i < count; ++i) {
        while (events[i].at - events[first].at >= BLOCK) ++first;
        if (i - first >= CAPACITY) { free(events); return 1; }
    }
    SF_INFO info = {.samplerate = (int)host->rate, .channels = 2, .format = SF_FORMAT_WAV | SF_FORMAT_FLOAT};
    SNDFILE *file = sf_open(path, SFM_WRITE, &info);
    if (!file) { free(events); return 1; }
    Source source = {.host = host, .events = events, .count = count};
    int result = 0;
    for (uint64_t at = 0; at < frames;) {
        float left[BLOCK], right[BLOCK], interleaved[2 * BLOCK];
        unsigned n = frames - at < BLOCK ? (unsigned)(frames - at) : BLOCK;
        process(&source, at, n, left, right);
        for (unsigned i = 0; i < n; ++i) {
            interleaved[2*i] = left[i];
            interleaved[2*i+1] = right[i];
        }
        if (sf_writef_float(file, interleaved, n) != n) { result = 1; break; }
        at += n;
    }
    if (sf_close(file)) result = 1;
    free(events);
    return result;
}

int main(int argc, char **argv) {
    bool probe = argc == 2 && !strcmp(argv[1], "probe");
    bool realtime = argc == 2 + CONTROLS + 1 && !strcmp(argv[1], "live");
    bool offline = argc == 2 + CONTROLS + 3 && !strcmp(argv[1], "render");
    if (!probe && !realtime && !offline) {
        fprintf(stderr, "Expected probe, live RATE CONTROLS, or render RATE PATH FRAMES CONTROLS.\n");
        return 2;
    }
    Host host = {0};
    const LilvPlugin *plugin = discover(&host, probe);
    int result = 1;
    if (!plugin) {
        fprintf(stderr, "Unsupported ePiano URI, version, features or port layout.\n");
        goto done;
    }
    if (probe) { result = 0; goto done; }
    char *end;
    unsigned long rate = strtoul(argv[2], &end, 10);
    if (*end || rate < 8000 || rate > 192000) goto done;
    host.rate = (unsigned)rate;
    uint64_t frames = offline ? strtoull(argv[4], &end, 10) : 0;
    if (offline && (*end || !frames || frames > (uint64_t)host.rate * 3600 ||
                    frames > (UINT32_MAX - 1023u) / 8u)) goto done;
    for (unsigned i = 0; i < CONTROLS; ++i) {
        float value = strtof(argv[(offline ? 5 : 3) + i], &end);
        if (*end || !isfinite(value) || value < 0 || value > 1) goto done;
        host.controls[i] = value;
    }
    host.plugin = lilv_plugin_instantiate(plugin, rate, features);
    if (!host.plugin) goto done;
    for (unsigned i = 0; i < CONTROLS; ++i)
        lilv_instance_connect_port(host.plugin, i, &host.controls[i]);
    clear_sequence(&host);
    lilv_instance_connect_port(host.plugin, 14, sequence(&host));
    lilv_instance_activate(host.plugin);
    result = realtime ? live(&host) : render(&host, argv[3], frames);
done:
    if (host.jack) jack_client_close(host.jack);
    if (host.queue) jack_ringbuffer_free(host.queue);
    if (host.plugin) {
        lilv_instance_deactivate(host.plugin);
        lilv_instance_free(host.plugin);
    }
    if (host.world) lilv_world_free(host.world);
    if (result) fprintf(stderr, "EPiano host operation failed.\n");
    return result;
}
