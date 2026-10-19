/* Real signal only. Capture is a side branch: the original audio output is untouched. */
(() => {
  window.DiggerAudioMeter = function (audio, elements) {
    const {root, left, right, leftLabel, rightChannel, status} = elements;
    let context = null;
    let stream = null;
    let nodes = [];
    let analysers = [];
    let buffers = [];
    let raf = null;
    let gesture = false;
    let disabled = false;
    let lastFrame = 0;
    let levels = [0, 0];

    function draw(values) {
      if (left) left.style.transform = `scaleX(${values[0] || 0})`;
      if (right) right.style.transform = `scaleX(${values[1] || 0})`;
    }
    function pause() {
      if (raf !== null) window.cancelAnimationFrame(raf);
      raf = null;
      levels = [0, 0];
      draw(levels);
    }
    function reset() {
      pause();
      for (const node of nodes) { try { node.disconnect(); } catch (_) {} }
      nodes = []; analysers = []; buffers = [];
      if (stream) {
        try { stream.getTracks().forEach(track => track.stop()); } catch (_) {}
      }
      stream = null;
    }
    function unavailable() {
      reset();
      disabled = true;
      if (root) root.dataset.mode = 'unavailable';
      if (status) status.textContent = 'VU indisponible dans ce navigateur';
      // No connection to, suspension of, or replacement of the audible element.
    }
    function sample(analyser, buffer) {
      if (analyser.getFloatTimeDomainData) analyser.getFloatTimeDomainData(buffer);
      else analyser.getByteTimeDomainData(buffer);
      let sum = 0;
      for (const raw of buffer) {
        const value = analyser.getFloatTimeDomainData ? raw : (raw - 128) / 128;
        sum += value * value;
      }
      const volume = audio.muted ? 0 : Math.max(0, Math.min(1, audio.volume));
      const rms = Math.sqrt(sum / buffer.length) * volume;
      // RMS in dBFS, displayed over -60..0 dB; no synthetic activity at silence.
      return rms > 0 ? Math.max(0, Math.min(1, (20 * Math.log10(rms) + 60) / 60)) : 0;
    }
    function frame(timestamp) {
      raf = null;
      if (disabled || audio.paused || audio.ended || context.state !== 'running') { pause(); return; }
      try {
        if (timestamp - lastFrame >= 33) {
          lastFrame = timestamp;
          const measured = analysers.map((analyser, i) => sample(analyser, buffers[i]));
          levels = measured.map((value, i) => value > levels[i] ? value : levels[i] * 0.65 + value * 0.35);
          draw(levels);
        }
        raf = window.requestAnimationFrame(frame);
      } catch (_) { unavailable(); }
    }
    function connect() {
      if (disabled || !gesture || !context || !stream || analysers.length) return;
      if (!stream.getAudioTracks().length) return;
      try {
        const source = context.createMediaStreamSource(stream);
        nodes.push(source);
        // Analysis sink is muted, preventing a second audible copy of the stream.
        const silent = context.createGain();
        silent.gain.value = 0;
        silent.connect(context.destination);
        nodes.push(silent);
        let splitter = null;
        if (context.createChannelSplitter) {
          try { splitter = context.createChannelSplitter(2); source.connect(splitter); nodes.push(splitter); }
          catch (_) { splitter = null; }
        }
        const count = splitter ? 2 : 1;
        if (leftLabel) leftLabel.textContent = count === 2 ? 'L' : 'NIVEAU';
        if (rightChannel) rightChannel.hidden = count === 1;
        for (let i = 0; i < count; i++) {
          const analyser = context.createAnalyser();
          analyser.fftSize = 512;
          analyser.smoothingTimeConstant = 0;
          if (splitter) splitter.connect(analyser, i); else source.connect(analyser);
          analyser.connect(silent);
          analysers.push(analyser); nodes.push(analyser);
          buffers.push(analyser.getFloatTimeDomainData ? new Float32Array(analyser.fftSize) : new Uint8Array(analyser.fftSize));
        }
        if (root) root.dataset.mode = count === 2 ? 'stereo' : 'global';
        if (status) status.textContent = '';
        playing();
      } catch (_) { unavailable(); }
    }
    function playing() {
      if (disabled || !gesture || !context || audio.paused) return;
      try {
        if (!stream) {
          const capture = audio.captureStream || audio.mozCaptureStream;
          stream = capture.call(audio);
          stream.addEventListener?.('addtrack', connect);
        }
        connect();
        if (analysers.length && raf === null) raf = window.requestAnimationFrame(frame);
      } catch (_) { unavailable(); }
    }
    function onGesture() {
      if (disabled || !root) return;
      gesture = true;
      try {
        const Context = window.AudioContext || window.webkitAudioContext;
        // Native Safari HLS is kept untouched when capture is unsupported.
        if (!Context || !(audio.captureStream || audio.mozCaptureStream) ||
            !window.requestAnimationFrame || !window.cancelAnimationFrame) { unavailable(); return; }
        if (!context) context = new Context();
        // Called synchronously in the click handler; never awaited before play().
        const resumed = context.resume();
        if (resumed && resumed.then) resumed.then(playing).catch(unavailable);
      } catch (_) { unavailable(); }
    }
    return {onGesture, playing, pause, reset};
  };
})();
