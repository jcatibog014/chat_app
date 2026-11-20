// The live connection: one WebSocket per tab (the browser side of the WebSocket lesson).
//
//   Live.request("message.send", {...})  -> a Promise: the server's ack, or its error
//   Live.on("message.new", (data) => ...) -> news the server pushes
//
// - Requests carry a "ref" so each answer finds its question, with many in flight.
// - A request made while offline WAITS in `pending` and is sent when the socket opens
//   again. That is safe because every request can be repeated: a message has its own
//   client_id (saved once), "read" never goes backwards, "sync" only reads.
// - Heartbeat: a "ping" every 25 s. No "pong" within 10 s means the connection is dead
//   even if the browser hasn't noticed (a laptop lid, a lost Wi-Fi): close and reconnect.
// - Reconnect: wait 1 s, 2 s, 4 s ... up to 30 s, each a random amount between half and
//   all of that ("jitter"), so a thousand tabs don't all knock at the same second after
//   a restart. "Reconnecting..." shows only if it takes more than 2 s.
// - Close code 4001 means "logged out" (a password change, Log out other devices): reload.
const Live = (() => {
  const HEARTBEAT = 25000;
  const PONG_WAIT = 10000;
  const listeners = new Map();
  const pending = new Map(); // ref -> { payload, resolve, reject, timer }; a Map keeps their order
  let socket = null;
  let attempt = 0;
  let refCount = 0;
  let everOpened = false;
  let stopped = false;
  let heartbeat = null;
  let pongTimer = null;
  let reconnectTimer = null;
  let noticeTimer = null;

  function on(type, fn) {
    if (!listeners.has(type)) listeners.set(type, []);
    listeners.get(type).push(fn);
  }

  function emit(type, data, frame) {
    (listeners.get(type) || []).forEach((fn) => {
      try {
        fn(data, frame);
      } catch (error) {
        console.error(error); // one broken listener mustn't stop the others
      }
    });
  }

  const address = () => `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`;
  const isOpen = () => socket !== null && socket.readyState === WebSocket.OPEN;
  const rawSend = (payload) => socket.send(JSON.stringify(payload));

  function connect() {
    clearTimeout(reconnectTimer);
    if (stopped || (socket && socket.readyState <= WebSocket.OPEN)) return;
    try {
      socket = new WebSocket(address());
    } catch (error) {
      socket = null;
      schedule();
      return;
    }
    socket.addEventListener("open", opened);
    socket.addEventListener("message", received);
    socket.addEventListener("close", closed);
  }

  function opened() {
    const again = everOpened;
    everOpened = true;
    attempt = 0;
    clearTimeout(noticeTimer);
    emit("status", "online");
    pending.forEach((entry) => rawSend(entry.payload)); // whatever waited while we were away
    startHeartbeat();
    if (again) emit("reconnected");
  }

  function received(event) {
    let frame;
    try {
      frame = JSON.parse(event.data);
    } catch (error) {
      return;
    }
    if (frame.type === "pong") {
      clearTimeout(pongTimer);
      return;
    }
    if ((frame.type === "ack" || frame.type === "error") && frame.ref && pending.has(frame.ref)) {
      const entry = pending.get(frame.ref);
      pending.delete(frame.ref);
      clearTimeout(entry.timer);
      if (frame.type === "ack") entry.resolve(frame.data || {});
      else entry.reject(frame);
      return;
    }
    emit(frame.type, frame.data || {}, frame);
  }

  function closed(event) {
    stopHeartbeat();
    socket = null;
    if (event.code === 4001) {
      stopped = true;
      location.reload(); // logged out: the page will go to the login
      return;
    }
    if (event.code === 4008) {
      stopped = true;
      emit("status", "too-many");
      return;
    }
    schedule();
  }

  function schedule() {
    if (stopped) return;
    const ceiling = Math.min(30000, 1000 * 2 ** attempt);
    const delay = ceiling / 2 + Math.random() * (ceiling / 2);
    attempt += 1;
    if (!noticeTimer || attempt === 1) {
      clearTimeout(noticeTimer);
      noticeTimer = setTimeout(() => emit("status", "reconnecting"), 2000);
    }
    reconnectTimer = setTimeout(connect, delay);
    if (attempt === 3) checkLogin();
  }

  // A refused handshake looks like any failed connection to a page. After a few tries, ask
  // whether we are still logged in at all.
  async function checkLogin() {
    try {
      const response = await fetch("/me.json", { headers: { Accept: "application/json" } });
      if (response.status === 401) {
        stopped = true;
        location.href = "/login";
      }
    } catch (error) {
      // Offline: keep trying to reconnect.
    }
  }

  function startHeartbeat() {
    stopHeartbeat();
    heartbeat = setInterval(() => {
      if (!isOpen()) return;
      rawSend({ type: "ping" });
      clearTimeout(pongTimer);
      pongTimer = setTimeout(() => socket && socket.close(), PONG_WAIT);
    }, HEARTBEAT);
  }

  function stopHeartbeat() {
    clearInterval(heartbeat);
    clearTimeout(pongTimer);
  }

  // Send something that wants an answer. timeout (ms) gives up on it; 0 waits for a connection.
  function request(type, data, { timeout = 0 } = {}) {
    const ref = `c${++refCount}`;
    const payload = { type, ref, data };
    return new Promise((resolve, reject) => {
      const entry = { payload, resolve, reject, timer: null };
      if (timeout) {
        entry.timer = setTimeout(() => {
          pending.delete(ref);
          reject({ code: "timeout", message: "The server didn't answer. Please try again." });
        }, timeout);
      }
      pending.set(ref, entry);
      if (isOpen()) rawSend(payload);
    });
  }

  // Send something that wants no answer (typing, presence). Dropped while offline.
  function tell(type, data) {
    if (isOpen()) rawSend({ type, data });
  }

  window.addEventListener("online", () => {
    if (!isOpen() && !stopped) {
      attempt = 0;
      connect();
    }
  });

  return { start: connect, on, request, tell, isOpen };
})();
