// public/main.js
import { RealtimeClient } from 'https://unpkg.com/@openai/realtime-api-beta?module';



const startBtn = document.getElementById('start');
const stopBtn  = document.getElementById('stop');
const transcriptDiv = document.getElementById('transcript');
let rt, mediaStream, audioCtx, proc, player;

class AudioPlayer {
  constructor() {
    this.chunks = [];
    this.ctx = new AudioContext({ sampleRate: 24000 });
  }
  playDelta(base64) {
    const bytes = Uint8Array.from(atob(base64), c => c.charCodeAt(0));
    this.chunks.push(bytes);
  }
  async flush() {
    const total = this.chunks.reduce((a,b) => a + b.length, 0);
    const concat = new Uint8Array(total);
    let offset = 0;
    for (const chunk of this.chunks) {
      concat.set(chunk, offset);
      offset += chunk.length;
    }
    const buf = await this.ctx.decodeAudioData(concat.buffer);
    const src = this.ctx.createBufferSource();
    src.buffer = buf;
    src.connect(this.ctx.destination);
    src.start();
    this.chunks = [];
  }
}

async function createSession() {
  const res = await fetch('/create_session', {
    method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({})
  });
  return res.json();
}

async function initRealtime() {
  const sess = await createSession();
  rt = new RealtimeClient({
    apiKey: sess.clientSecret,
    model: sess.model,
    modalities: sess.modalities,
    voice: sess.voice,
    instructions: sess.instructions
  });
  await rt.connect();
  player = new AudioPlayer();
  rt.on('transcription', m => transcriptDiv.textContent = m.transcript);
  rt.on('audio', d => player.playDelta(d));
  rt.on('done', () => player.flush());
}

function startAudio() {
  audioCtx = new AudioContext({ sampleRate:24000 });
  return navigator.mediaDevices.getUserMedia({audio:true}).then(stream => {
    mediaStream = stream;
    const src = audioCtx.createMediaStreamSource(stream);
    proc = audioCtx.createScriptProcessor(4096,1,1);
    src.connect(proc); proc.connect(audioCtx.destination);
    proc.onaudioprocess = e => {
      const f = e.inputBuffer.getChannelData(0);
      const i16 = new Int16Array(f.length);
      for (let i=0; i<f.length; i++) i16[i] = Math.max(-32768,Math.min(32767,f[i]*32767));
      rt.sendAudio(i16.buffer);
    };
  });
}

startBtn.onclick = async () => { startBtn.disabled=true; stopBtn.disabled=false; await initRealtime(); await startAudio(); };
stopBtn.onclick = () => { stopBtn.disabled=true; proc.disconnect(); mediaStream.getTracks().forEach(t=>t.stop()); };
