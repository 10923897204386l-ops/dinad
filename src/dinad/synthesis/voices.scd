// Pure native synthesis. Python supplies frequency, onset and gate release.
[
    SynthDef(\dinad_drone, { |out=16, freq=110, amp=0.035, gate=1|
        var env = EnvGen.kr(Env.asr(0.02, 1, 0.3), gate, doneAction: 2);
        Out.ar(out, (SinOsc.ar(freq) * amp * env) ! 2);
    }),
    SynthDef(\dinad_pad, { |out=16, freq=220, amp=0.025, gate=1|
        var env = EnvGen.kr(Env.asr(0.02, 1, 0.4), gate, doneAction: 2);
        var sig = LPF.ar(VarSaw.ar(freq * [0.998, 1.002], 0, 0.4), 1800);
        Out.ar(out, sig * amp * env);
    }),
    SynthDef(\dinad_arp, { |out=16, freq=440, amp=0.04, gate=1|
        var env = EnvGen.kr(Env.asr(0.005, 1, 0.08), gate, doneAction: 2);
        Out.ar(out, (SinOsc.ar(freq) * amp * env) ! 2);
    }),
    SynthDef(\dinad_effects, { |in=16, out=0|
        var dry = In.ar(in, 2);
        var delayed = dry + (DelayC.ar(dry, 0.3, 0.25) * 0.2);
        var signal = FreeVerb2.ar(delayed[0], delayed[1], 0.2, 0.5, 0.3);
        Out.ar(out, LeakDC.ar(signal).tanh);
    })
]
