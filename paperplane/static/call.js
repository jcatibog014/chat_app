// Calls: audio, video and group calls (calls.py on the server).
//
// The sound and pictures go straight between the browsers (WebRTC). The server rings people and
// passes each browser's connection notes to the others: an "offer" and an "answer" describing
// the media (SDP), then "ICE candidates", the addresses each browser may be reached at.
// In a group call everyone connects to everyone: the person who JOINS sends an offer to each
// person already in the call (so two people never both offer to each other at once).
// Calls need a page served over https (or 127.0.0.1) and permission for the microphone/camera.
(() => {
  if (typeof Live === "undefined" || !window.RTCPeerConnection || !navigator.mediaDevices) return;
  const me = Number(document.body.dataset.me);
  const say = (text, bad) => (typeof toast === "function" ? toast(text, bad) : null);
  document.querySelectorAll("[data-call-start]").forEach((button) => (button.hidden = false));

  let call = null;      // the call this tab is in
  let ringing = null;   // an incoming call, still ringing here
  let screen = null;    // the call screen
  let ringer = null;    // the ringing sound

  // ---------- A small ringing sound, made here (no sound file) ----------
  function ring(outgoing) {
    stopRing();
    try {
      const audio = new (window.AudioContext || window.webkitAudioContext)();
      const beep = () => {
        const tone = audio.createOscillator();
        const volume = audio.createGain();
        tone.frequency.value = outgoing ? 440 : 660;
        volume.gain.setValueAtTime(0.0001, audio.currentTime);
        volume.gain.exponentialRampToValueAtTime(0.08, audio.currentTime + 0.05);
        volume.gain.exponentialRampToValueAtTime(0.0001, audio.currentTime + (outgoing ? 1.0 : 0.6));
        tone.connect(volume).connect(audio.destination);
        tone.start();
        tone.stop(audio.currentTime + 1.1);
      };
      beep();
      const repeat = setInterval(beep, outgoing ? 3000 : 1500);
      ringer = { audio, repeat };
    } catch (error) {
      ringer = null;  // no sound: the screen still shows the call
    }
    // Phones buzz too (browsers allow it only after the person has touched the page).
    const touched = !navigator.userActivation || navigator.userActivation.hasBeenActive;
    if (!outgoing && navigator.vibrate && touched) navigator.vibrate([300, 200, 300]);
  }
  function stopRing() {
    if (!ringer) return;
    clearInterval(ringer.repeat);
    ringer.audio.close().catch(() => {});
    ringer = null;
  }

  // ---------- Microphone and camera ----------
  async function getMedia(kind) {
    try {
      return await navigator.mediaDevices.getUserMedia({ audio: true, video: kind === "video" ? { facingMode: "user" } : false });
    } catch (error) {
      if (kind === "video") {
        try {
          const audioOnly = await navigator.mediaDevices.getUserMedia({ audio: true });
          say("Your camera isn't available, so you're on the call with sound only.");
          return audioOnly;
        } catch (ignored) { /* fall through */ }
      }
      say("To call, allow this site to use your microphone" + (kind === "video" ? " and camera." : "."), true);
      return null;
    }
  }

  // ---------- The call screen ----------
  const icon = (name) => {
    const source = document.querySelector(`[data-call-icon="${name}"]`);
    return source ? source.innerHTML : "";
  };

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text) node.textContent = text;
    return node;
  }

  function buildScreen(title, kind) {
    screen = el("div", `call-screen is-${kind}`);
    screen.setAttribute("role", "dialog");
    screen.setAttribute("aria-label", `${kind === "video" ? "Video" : "Audio"} call: ${title}`);
    const top = el("div", "call-top");
    const heading = el("div", "call-heading");
    heading.append(el("strong", "", title), el("span", "call-status", "Calling..."));
    const minimize = el("button", "call-round call-small");
    minimize.type = "button";
    minimize.title = "Minimise the call";
    minimize.innerHTML = icon("minimize") + '<span class="visually-hidden">Minimise the call</span>';
    minimize.addEventListener("click", () => screen.classList.toggle("is-small"));
    top.append(heading, minimize);
    const tiles = el("div", "call-tiles");
    const controls = el("div", "call-controls");
    const mic = el("button", "call-round");
    mic.type = "button";
    mic.dataset.callMic = "";
    const camera = el("button", "call-round");
    camera.type = "button";
    camera.dataset.callCamera = "";
    const hangUp = el("button", "call-round call-end");
    hangUp.type = "button";
    hangUp.title = "End call";
    hangUp.innerHTML = icon("phone-off") + '<span class="visually-hidden">End call</span>';
    hangUp.addEventListener("click", () => leave());
    mic.addEventListener("click", toggleMic);
    camera.addEventListener("click", toggleCamera);
    controls.append(mic, camera, hangUp);
    screen.append(top, tiles, controls);
    const box = screen;  // the handler keeps its own element: End call clears `screen` during this click
    box.addEventListener("click", (event) => {
      if (box.classList.contains("is-small") && !event.target.closest("button")) box.classList.remove("is-small");
    });
    document.body.append(screen);
    document.body.classList.add("in-call");
    drawButtons();
  }

  function status(text) {
    if (screen) screen.querySelector(".call-status").textContent = text;
  }

  function drawButtons() {
    if (!screen || !call) return;
    const audioOn = call.local.getAudioTracks().some((t) => t.enabled);
    const videoTrack = call.local.getVideoTracks()[0];
    const videoOn = Boolean(videoTrack && videoTrack.enabled);
    const mic = screen.querySelector("[data-call-mic]");
    mic.innerHTML = icon(audioOn ? "mic" : "mic-off") + `<span class="visually-hidden">${audioOn ? "Mute" : "Unmute"}</span>`;
    mic.title = audioOn ? "Mute" : "Unmute";
    mic.classList.toggle("is-off", !audioOn);
    const camera = screen.querySelector("[data-call-camera]");
    camera.hidden = !videoTrack;
    camera.innerHTML = icon(videoOn ? "video" : "video-off") + `<span class="visually-hidden">${videoOn ? "Turn the camera off" : "Turn the camera on"}</span>`;
    camera.title = videoOn ? "Turn the camera off" : "Turn the camera on";
    camera.classList.toggle("is-off", !videoOn);
    const mine = screen.querySelector(".call-tile.is-me");
    if (mine) mine.classList.toggle("is-camera-off", !videoOn);
  }

  function tile(user, stream, isMe) {
    if (!screen) return null;  // a late event after the call ended
    let box = screen.querySelector(`.call-tile[data-user="${user.id}"]`);
    if (!box) {
      box = el("div", `call-tile${isMe ? " is-me" : ""}`);
      box.dataset.user = user.id;
      const face = el("span", `call-face avatar tone-${user.tone || 1}`, (user.first || user.name || "?").charAt(0));
      const video = el("video");
      video.autoplay = true;
      video.playsInline = true;
      if (isMe) video.muted = true;  // never hear yourself
      const name = el("span", "call-name", isMe ? "You" : user.name);
      const muted = el("span", "call-muted");
      muted.innerHTML = icon("mic-off");
      muted.hidden = true;
      box.append(face, video, name, muted);
      screen.querySelector(".call-tiles").append(box);
      screen.querySelector(".call-tiles").dataset.count = screen.querySelectorAll(".call-tile").length;
    }
    if (stream) {
      const video = box.querySelector("video");
      if (video.srcObject !== stream) video.srcObject = stream;
      const hasVideo = stream.getVideoTracks().some((t) => t.readyState === "live");
      box.classList.toggle("has-video", hasVideo);
      video.play().catch(() => {});
    }
    return box;
  }

  function removeTile(userId) {
    const box = screen && screen.querySelector(`.call-tile[data-user="${userId}"]`);
    if (box) box.remove();
    if (screen) screen.querySelector(".call-tiles").dataset.count = screen.querySelectorAll(".call-tile").length;
  }

  // ---------- Connections, one per person ----------
  function send(to, data) {
    Live.request("call.signal", { call_id: call.id, to, data }).catch(() => {});
  }

  function peer(user, offerer) {
    const existing = call.peers.get(user.id);
    if (existing) return existing;
    const pc = new RTCPeerConnection({ iceServers: call.iceServers });
    const entry = { pc, user, waiting: [], ready: false };
    call.peers.set(user.id, entry);
    call.local.getTracks().forEach((track) => pc.addTrack(track, call.local));
    pc.onicecandidate = (event) => event.candidate && send(user.id, { candidate: event.candidate.toJSON() });
    pc.ontrack = (event) => tile(user, event.streams[0], false);
    pc.onconnectionstatechange = () => {
      if (pc.connectionState === "connected") connected();
      if (pc.connectionState === "failed") {
        const box = tile(user, null, false);
        if (box) box.classList.add("is-failed");
        if (call && call.peers.size === 1) status("Couldn't connect. One of your networks may need a relay server.");
      }
    };
    tile(user, null, false);
    if (offerer) {
      pc.createOffer()
        .then((offer) => pc.setLocalDescription(offer))
        .then(() => send(user.id, { sdp: pc.localDescription.toJSON() }))
        .catch(() => status("Couldn't start the connection."));
    }
    return entry;
  }

  async function onSignal(from, data) {
    let entry = call.peers.get(from);
    if (!entry) entry = peer(call.people.get(from) || { id: from, name: "Someone" }, false);
    const { pc } = entry;
    try {
      if (data.sdp) {
        await pc.setRemoteDescription(data.sdp);
        entry.ready = true;
        for (const candidate of entry.waiting.splice(0)) await pc.addIceCandidate(candidate).catch(() => {});
        if (data.sdp.type === "offer") {
          await pc.setLocalDescription(await pc.createAnswer());
          send(from, { sdp: pc.localDescription.toJSON() });
        }
      } else if (data.candidate) {
        if (entry.ready) await pc.addIceCandidate(data.candidate).catch(() => {});
        else entry.waiting.push(data.candidate);  // too early: keep it until the description arrives
      }
    } catch (error) {
      status("Couldn't connect to everyone.");
    }
  }

  function connected() {
    stopRing();
    if (!call || call.timerStarted) return;
    call.timerStarted = Date.now();
    clearTimeout(call.giveUp);
    const tick = () => {
      if (!call) return;
      const seconds = Math.floor((Date.now() - call.timerStarted) / 1000);
      status(`${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`);
    };
    tick();
    call.clock = setInterval(tick, 1000);
  }

  // ---------- Starting, answering, ending ----------
  function begin(data, local, answer, title) {
    call = { id: data.call.id, conversationId: data.call.conversation_id, kind: data.call.kind, local,
      iceServers: answer.ice_servers || [], peers: new Map(), people: new Map(), timerStarted: 0 };
    (data.call.people || []).forEach((p) => call.people.set(p.id, p));
    buildScreen(title, call.kind);
    tile({ id: me, name: "You", first: (document.body.dataset.meName || "Y"), tone: document.body.dataset.meTone }, local, true);
    drawButtons();
    window.addEventListener("beforeunload", warnLeaving);
  }

  async function start(kind) {
    if (call) return say("You're already in a call.");
    const section = document.querySelector("[data-conversation]");
    if (!section) return;
    const local = await getMedia(kind);
    if (!local) return;
    let answer;
    try {
      answer = await Live.request("call.start", { conversation_id: Number(section.dataset.conversation), kind });
    } catch (error) {
      local.getTracks().forEach((t) => t.stop());
      return say(error.message || "Couldn't start the call.", true);
    }
    const title = document.querySelector(".conversation-title h2").textContent.trim();
    if (answer.join) {
      local.getTracks().forEach((t) => t.stop());
      return join(answer.call.id, answer.call.kind, title);
    }
    begin(answer, local, answer, title);
    status("Calling...");
    ring(true);
    call.giveUp = setTimeout(() => {
      if (call && !call.timerStarted && call.peers.size === 0) leave("No answer.");
    }, (answer.ring_seconds || 45) * 1000);
  }

  async function join(callId, kind, title) {
    if (call) return say("You're already in a call.");
    closeIncoming();
    const local = await getMedia(kind);
    if (!local) {
      Live.request("call.decline", { call_id: callId }).catch(() => {});
      return;
    }
    let answer;
    try {
      answer = await Live.request("call.join", { call_id: callId });
    } catch (error) {
      local.getTracks().forEach((t) => t.stop());
      return say(error.message || "Couldn't join the call.", true);
    }
    begin(answer, local, answer, title);
    status("Connecting...");
    // I'm the newcomer: I offer to everyone already here.
    answer.call.people.filter((p) => p.id !== me).forEach((p) => peer(p, true));
  }

  function teardown(words) {
    if (!call) return;
    clearTimeout(call.giveUp);
    clearInterval(call.clock);
    call.peers.forEach(({ pc }) => pc.close());
    call.local.getTracks().forEach((t) => t.stop());
    call = null;
    stopRing();
    if (screen) screen.remove();
    screen = null;
    document.body.classList.remove("in-call");
    window.removeEventListener("beforeunload", warnLeaving);
    if (words) say(words);
  }

  function leave(words) {
    if (!call) return;
    const id = call.id;
    teardown(words || "Call ended.");
    Live.request("call.leave", { call_id: id }).catch(() => {});
  }

  function warnLeaving(event) {
    event.preventDefault();
    event.returnValue = "";  // leaving this page ends the call
  }

  function toggleMic() {
    if (!call) return;
    const tracks = call.local.getAudioTracks();
    const on = !tracks.some((t) => t.enabled);
    tracks.forEach((t) => (t.enabled = on));
    tellMedia();
    drawButtons();
  }

  function toggleCamera() {
    if (!call) return;
    const tracks = call.local.getVideoTracks();
    const on = !tracks.some((t) => t.enabled);
    tracks.forEach((t) => (t.enabled = on));
    tellMedia();
    drawButtons();
  }

  function tellMedia() {
    Live.request("call.media", { call_id: call.id, audio: call.local.getAudioTracks().some((t) => t.enabled),
      video: call.local.getVideoTracks().some((t) => t.enabled) }).catch(() => {});
  }

  // ---------- An incoming call ----------
  function showIncoming(data) {
    closeIncoming();
    const box = el("div", "call-incoming");
    box.setAttribute("role", "alertdialog");
    box.setAttribute("aria-label", "Incoming call");
    const who = el("div", "call-incoming-who");
    const face = el("span", `avatar avatar-big tone-${data.from.tone || 1}`, data.from.first.charAt(0));
    const words = el("div", "call-incoming-words");
    words.append(el("strong", "", data.title),
      el("span", "", data.is_group ? `${data.from.first} started a group ${data.kind} call` : `${data.kind === "video" ? "Video" : "Audio"} call`));
    who.append(face, words);
    const buttons = el("div", "call-incoming-buttons");
    const no = el("button", "call-round call-end");
    no.type = "button";
    no.title = "Decline";
    no.innerHTML = icon("phone-off") + "<span>Decline</span>";
    const yes = el("button", "call-round call-answer");
    yes.type = "button";
    yes.title = "Answer";
    yes.innerHTML = icon(data.kind === "video" ? "video" : "phone") + "<span>Answer</span>";
    no.addEventListener("click", () => {
      Live.request("call.decline", { call_id: data.call_id }).catch(() => {});
      closeIncoming();
    });
    yes.addEventListener("click", () => join(data.call_id, data.kind, data.title));
    buttons.append(no, yes);
    box.append(who, buttons);
    document.body.append(box);
    ringing = { id: data.call_id, box, timer: setTimeout(closeIncoming, 50 * 1000) };
    ring(false);
    yes.focus({ preventScroll: true });
  }

  function closeIncoming() {
    if (!ringing) return;
    clearTimeout(ringing.timer);
    ringing.box.remove();
    ringing = null;
    if (!call) stopRing();
  }

  // ---------- What the server tells us ----------
  Live.on("call.ring", (data) => {
    if (call) return;  // busy: the caller hears no answer
    showIncoming(data);
  });
  Live.on("call.taken", (data) => {
    if (ringing && ringing.id === data.call_id) closeIncoming();  // answered or started in another tab
  });
  Live.on("call.joined", (data) => {
    if (!call || data.call_id !== call.id) return;
    call.people.set(data.user.id, data.user);
    clearTimeout(call.giveUp);
    stopRing();
    status("Connecting...");
    tile(data.user, null, false);  // their offer is on its way
  });
  Live.on("call.signal", (data) => {
    if (call && data.call_id === call.id) onSignal(data.from, data.data);
  });
  Live.on("call.left", (data) => {
    if (!call || data.call_id !== call.id) return;
    const entry = call.peers.get(data.user_id);
    if (entry) entry.pc.close();
    call.peers.delete(data.user_id);
    removeTile(data.user_id);
    if (!call.peers.size) status("Waiting for others...");
  });
  Live.on("call.declined", (data) => {
    if (call && data.call_id === call.id) teardown(`${data.user.first} can't take your call right now.`);
  });
  Live.on("call.ended", (data) => {
    if (ringing && ringing.id === data.call_id) closeIncoming();
    if (call && data.call_id === call.id) teardown("Call ended.");
  });
  Live.on("call.media", (data) => {
    if (!call || data.call_id !== call.id || !screen) return;
    const box = screen.querySelector(`.call-tile[data-user="${data.user_id}"]`);
    if (!box) return;
    box.querySelector(".call-muted").hidden = data.audio;
    box.classList.toggle("is-camera-off", !data.video);
  });
  // A call going on in the chat I'm looking at: show (or hide) its Join bar.
  Live.on("call.state", (data) => {
    const section = document.querySelector("[data-conversation]");
    const banner = document.querySelector("[data-call-banner]");
    if (!section || !banner || Number(section.dataset.conversation) !== data.conversation_id) return;
    const going = Boolean(data.call_id) && !(call && call.id === data.call_id);
    banner.hidden = !going;
    if (going) {
      banner.dataset.callId = data.call_id;
      banner.dataset.callKind = data.kind;
      banner.querySelector("[data-call-banner-words]").textContent =
        `${data.kind === "video" ? "Video" : "Audio"} call going on · ${data.count} in the call`;
    }
  });

  // ---------- The buttons on the page ----------
  document.addEventListener("click", (event) => {
    const startButton = event.target.closest("[data-call-start]");
    if (startButton) start(startButton.dataset.callStart);
    const joinButton = event.target.closest("[data-call-join]");
    if (joinButton) {
      const banner = joinButton.closest("[data-call-banner]");
      const title = document.querySelector(".conversation-title h2").textContent.trim();
      join(Number(banner.dataset.callId), banner.dataset.callKind, title);
    }
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && ringing) closeIncoming();
  });
})();
