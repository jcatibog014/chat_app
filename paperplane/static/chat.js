// The chat pages, live: sending over the WebSocket, new messages arriving, unread counts,
// and catching up after a reconnect. Without this file every page still works (forms).
//
// Messages arrive with their HTML already drawn by the server (the same template as the
// page, escaped there), so this file never builds markup from what people typed: it only
// sets textContent for the "Sending..." bubble.
(() => {
  const body = document.body;
  const me = Number(body.dataset.me);
  if (!me || typeof Live === "undefined") return;

  const zone = body.dataset.timeZone || undefined;
  const clockFormat = new Intl.DateTimeFormat([], { hour: "numeric", minute: "2-digit", timeZone: zone });
  const dayKeyFormat = new Intl.DateTimeFormat("en-CA", { timeZone: zone, year: "numeric", month: "2-digit", day: "2-digit" });
  const weekdayFormat = new Intl.DateTimeFormat("en-US", { timeZone: zone, weekday: "long" });
  const dateFormat = new Intl.DateTimeFormat("en-US", { timeZone: zone, month: "short", day: "numeric" });
  const FOLLOW_GAP = 5 * 60 * 1000;

  const conversation = document.querySelector("[data-conversation]");
  const openId = conversation ? Number(conversation.dataset.conversation) : null;
  const list = document.querySelector("[data-chat-list]");
  const messages = document.querySelector("[data-messages]");
  const scroller = document.querySelector("[data-scroll]");
  const totalBadge = document.querySelector("[data-total-unread]");
  const connectionNote = document.querySelector("[data-connection]");
  const baseTitle = document.title.replace(/^\(\d+\) /, "");
  let version = conversation ? Number(conversation.dataset.version) : 0;
  let lastSeq = conversation ? Number(conversation.dataset.lastSeq) : 0;
  let readSeq = conversation ? Number(conversation.dataset.readSeq) : 0;
  let total = totalBadge ? Number(totalBadge.textContent) || 0 : 0;

  const fromHTML = (html) => {
    const template = document.createElement("template");
    template.innerHTML = html.trim();
    return template.content.firstElementChild;
  };

  const newId = () => {
    if (window.crypto && crypto.randomUUID) return crypto.randomUUID().replace(/-/g, "");
    const bytes = new Uint8Array(16);
    crypto.getRandomValues(bytes);
    return Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
  };

  // ---------- Times, in the reader's own time zone ----------

  function dayLabel(date) {
    const key = dayKeyFormat.format(date);
    const today = new Date();
    if (key === dayKeyFormat.format(today)) return "Today";
    if (key === dayKeyFormat.format(new Date(today - 864e5))) return "Yesterday";
    if (today - date < 6 * 864e5) return weekdayFormat.format(date);
    return dateFormat.format(date);
  }

  function localize(root) {
    root.querySelectorAll("time[data-clock]").forEach((time) => {
      time.textContent = clockFormat.format(new Date(time.dateTime));
    });
  }

  // ---------- Unread counts: each chat's badge, the menu's total, the tab's title ----------

  const rowOf = (id) => list && list.querySelector(`[data-chat="${id}"]`);

  function setRowUnread(row, count) {
    const badge = row.querySelector("[data-unread]");
    if (!badge) return;
    badge.querySelector("[data-unread-number]").textContent = count;
    badge.hidden = count <= 0;
    row.classList.toggle("has-unread", count > 0);
  }

  const rowUnread = (row) => Number(row.querySelector("[data-unread-number]").textContent) || 0;

  function setTotal(count) {
    total = Math.max(0, count);
    // The sky's Chats link and the phone's bottom bar both show it.
    document.querySelectorAll("[data-total-unread]").forEach((badge) => {
      badge.textContent = total;
      badge.hidden = total === 0;
    });
    document.title = total > 0 ? `(${total}) ${baseTitle}` : baseTitle;
  }

  // ---------- Reading ----------

  const nearBottom = () => !scroller || Math.abs(scroller.scrollTop) < 120; // column-reverse: 0 is the bottom
  const reading = () => openId !== null && document.visibilityState === "visible" && nearBottom();

  function markRead(seq) {
    if (!openId || seq <= readSeq) return;
    readSeq = seq;
    const row = rowOf(openId);
    if (row) setRowUnread(row, 0);
    Live.request("read", { conversation_id: openId, seq }).catch(() => {});
  }

  // ---------- Messages in the open chat ----------

  function lastMessageEl() {
    const all = messages.querySelectorAll(".msg");
    return all.length ? all[all.length - 1] : null;
  }

  // The server drew the message for everyone: make it yours if it is, join it to the run
  // before it, put a day line before it if a new day started, and write its time.
  function decorate(el, before) {
    if (el.dataset.sender && Number(el.dataset.sender) === me) {
      el.classList.remove("theirs");
      el.classList.add("mine");
    }
    if (before && !el.classList.contains("msg-system") && !before.classList.contains("msg-system")
        && before.dataset.sender === el.dataset.sender
        && new Date(el.dataset.at) - new Date(before.dataset.at) <= FOLLOW_GAP
        && dayKeyFormat.format(new Date(el.dataset.at)) === dayKeyFormat.format(new Date(before.dataset.at))) {
      el.classList.add("follows");
    }
    addTokens(el);
    markMyReactions(el);
    markMentions(el);
    localize(el);
  }

  function append(el) {
    const start = messages.querySelector("[data-chat-start]");
    if (start) start.remove();
    const before = lastMessageEl();
    const at = new Date(el.dataset.at);
    if (!before || dayKeyFormat.format(new Date(before.dataset.at)) !== dayKeyFormat.format(at)) {
      const line = document.createElement("li");
      line.className = "day-break";
      const label = document.createElement("span");
      label.textContent = dayLabel(at);
      line.append(label);
      messages.insertBefore(line, messages.querySelector(".latest-anchor"));
    }
    decorate(el, before);
    el.classList.add("is-new");
    messages.insertBefore(el, messages.querySelector(".latest-anchor"));
    drawSeen();
  }

  function showMessage(m) {
    if (!messages || m.conversation_id !== openId) return;
    version = Math.max(version, m.version);
    lastSeq = Math.max(lastSeq, m.seq);
    const existing = document.getElementById(`m-${m.id}`);
    const fresh = fromHTML(m.html);
    if (existing) {
      // An edit, a delete, a reaction (stage 6): the newer drawing replaces the old one.
      if (existing.classList.contains("follows")) fresh.classList.add("follows");
      decorate(fresh, null);
      existing.replaceWith(fresh);
      drawSeen();
      return;
    }
    const waiting = m.client_id && messages.querySelector(`[data-client-id="${m.client_id}"]`);
    if (waiting) {
      const before = waiting.previousElementSibling && waiting.previousElementSibling.classList.contains("msg")
        ? waiting.previousElementSibling : null;
      decorate(fresh, before);
      waiting.replaceWith(fresh);
      drawSeen();
      return;
    }
    append(fresh);
  }

  // ---------- The chat list ----------

  function updateRow(m) {
    if (!list) return;
    const row = rowOf(m.conversation_id);
    if (!row) {
      resync(); // a chat we don't know yet
      return;
    }
    const mine = m.sender_id === me;
    let prefix = "";
    if (m.kind !== "system") prefix = mine ? "You: " : row.dataset.kind === "group" && m.sender_first ? `${m.sender_first}: ` : "";
    row.querySelector("[data-preview]").textContent = prefix + m.preview;
    const when = row.querySelector("[data-when]");
    when.dateTime = m.created_at;
    when.textContent = clockFormat.format(new Date(m.created_at));
    row.dataset.lastSeq = m.seq;
    list.prepend(row);
  }

  function markOpenRow() {
    if (!list || !openId) return;
    const row = rowOf(openId);
    if (!row) return;
    row.classList.add("is-open");
    row.querySelector("a").setAttribute("aria-current", "page");
  }

  // ---------- Sending ----------

  function pendingBubble(text, clientId) {
    const li = document.createElement("li");
    li.className = "msg mine is-pending";
    li.dataset.clientId = clientId;
    li.dataset.sender = String(me);
    li.dataset.at = new Date().toISOString();
    li.innerHTML = '<span class="msg-avatar"></span><div class="msg-body"><div class="bubble">' +
      '<p class="msg-text"></p><span class="msg-meta">Sending...</span></div></div>';
    li.querySelector(".msg-text").textContent = text; // text, never HTML
    return li;
  }

  function sendMessage(text, clientId, el, replyTo) {
    el.classList.remove("is-failed");
    el.classList.add("is-pending");
    el.querySelector(".msg-meta").textContent = "Sending...";
    const data = { conversation_id: openId, body: text, client_id: clientId };
    if (replyTo) data.reply_to = replyTo;
    Live.request("message.send", data)
      .then((ack) => {
        const m = ack.message;
        readSeq = Math.max(readSeq, m.seq);
        if (document.getElementById(`m-${m.id}`)) {
          el.remove(); // message.new got here first and already put the saved copy in
        } else if (el.isConnected) {
          showMessage(m);
        }
      })
      .catch((error) => {
        el.classList.remove("is-pending");
        el.classList.add("is-failed");
        el.querySelector(".msg-meta").textContent = "Not sent. Tap to try again.";
        if (typeof toast === "function" && error && error.message) toast(error.message, true);
        el.addEventListener("click", () => sendMessage(text, clientId, el, replyTo), { once: true });
      });
  }

  // A picture or file goes up over HTTP (the WebSocket carries only small frames): the same
  // form the page uses without JavaScript, sent with fetch, answered with JSON.
  function uploadMessage(form, text, file, clientId, replyTo, el) {
    const data = new FormData();
    data.append("csrf_token", csrfToken);
    data.append("body", text);
    data.append("client_id", clientId);
    if (replyTo) data.append("reply_to", replyTo);
    data.append("file", file);
    el.querySelector(".msg-meta").textContent = "Uploading...";
    fetch(form.action, { method: "POST", body: data, headers: { Accept: "application/json", "X-CSRF-Token": csrfToken } })
      .then(async (response) => {
        const answer = await response.json().catch(() => ({}));
        if (!response.ok) throw { message: answer.message || "That file couldn't be sent." };
        const m = answer.message;
        if (document.getElementById(`m-${m.id}`)) el.remove();
        else if (el.isConnected) showMessage(m);
      })
      .catch((error) => {
        el.classList.remove("is-pending");
        el.classList.add("is-failed");
        el.querySelector(".msg-meta").textContent = "Not sent.";
        showError(error);
      });
  }

  function setUpComposer() {
    const form = document.querySelector("[data-composer]");
    if (!form || !openId) return;
    const area = form.querySelector("[data-composer-text]");
    const fileInput = form.querySelector("[data-attach-input]");
    const chip = document.querySelector("[data-attach-chip]");
    let thumbUrl = null;
    const clearFile = () => {
      if (fileInput) fileInput.value = "";
      if (chip) chip.hidden = true;
      if (thumbUrl) URL.revokeObjectURL(thumbUrl);
      thumbUrl = null;
    };
    if (fileInput && chip) {
      fileInput.addEventListener("change", () => {
        const file = fileInput.files[0];
        if (!file) return clearFile();
        if (file.size > 5 * 1000 * 1000) {
          showError({ message: "A file can be at most 5 MB." });
          return clearFile();
        }
        chip.querySelector("[data-attach-name]").textContent = `${file.name} (${Math.max(1, Math.round(file.size / 1000))} KB)`;
        const thumb = chip.querySelector("[data-attach-thumb]");
        if (thumbUrl) URL.revokeObjectURL(thumbUrl);
        thumbUrl = file.type.startsWith("image/") ? URL.createObjectURL(file) : null;
        thumb.hidden = !thumbUrl;
        if (thumbUrl) thumb.src = thumbUrl;
        chip.hidden = false;
        area.focus();
      });
      chip.querySelector("[data-attach-clear]").addEventListener("click", clearFile);
    }
    const fit = () => {
      area.style.height = "auto";
      area.style.height = `${Math.min(area.scrollHeight, 160)}px`;
    };
    area.addEventListener("input", fit);
    area.addEventListener("keydown", (event) => {
      // Enter sends; Shift+Enter is a new line. Not while an input method is composing a word.
      if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
        event.preventDefault();
        form.requestSubmit();
      }
    });
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      const text = area.value.trim();
      const file = fileInput && fileInput.files[0];
      if (!text && !file) return;
      const clientId = newId();
      const replyTo = replyInput && Number(replyInput.value) ? Number(replyInput.value) : null;
      const el = pendingBubble(text || file.name, clientId);
      append(el);
      area.value = "";
      fit();
      setReply(null);
      if (scroller) scroller.scrollTop = 0;
      if (file) {
        uploadMessage(form, text, file, clientId, replyTo, el);
        clearFile();
      } else {
        sendMessage(text, clientId, el, replyTo);
      }
      document.dispatchEvent(new CustomEvent("paperplane:sent"));
    });
  }

  // ---------- Catching up after a reconnect ----------

  let syncing = false;
  function resync() {
    if (syncing) return;
    syncing = true;
    Live.request("sync", { open: openId ? { conversation_id: openId, version } : null }, { timeout: 15000 })
      .then((answer) => {
        if (answer.gone) {
          location.href = "/chats";
          return;
        }
        if (answer.reload) {
          location.reload();
          return;
        }
        if (list) {
          list.innerHTML = answer.list_html;
          localize(list);
          markOpenRow();
          const empty = document.querySelector("[data-chat-list-empty]");
          if (empty && list.children.length) empty.remove();
        }
        setTotal(answer.total_unread);
        (answer.changes || []).forEach(showMessage);
        if (typeof answer.version === "number") version = Math.max(version, answer.version);
        if (reading()) markRead(lastSeq);
        document.dispatchEvent(new CustomEvent("paperplane:synced", { detail: answer }));
      })
      .catch(() => {})
      .finally(() => {
        syncing = false;
      });
  }

  // ---------- What the server tells us ----------

  Live.on("message.new", (m) => {
    const mine = m.sender_id === me;
    updateRow(m);
    if (m.conversation_id === openId) showMessage(m);
    if (mine) return;
    if (m.conversation_id === openId && reading()) {
      markRead(m.seq);
    } else {
      const row = rowOf(m.conversation_id);
      if (row && !row.dataset.muted) setRowUnread(row, rowUnread(row) + 1);
      if (!row || !row.dataset.muted) setTotal(total + 1);
    }
    document.dispatchEvent(new CustomEvent("paperplane:message", { detail: m }));
  });

  Live.on("message.updated", (m) => {
    showMessage(m);
    const row = rowOf(m.conversation_id);
    if (row && Number(row.dataset.lastSeq) === m.seq) {
      const mine = m.sender_id === me;
      const prefix = mine ? "You: " : row.dataset.kind === "group" && m.sender_first ? `${m.sender_first}: ` : "";
      row.querySelector("[data-preview]").textContent = prefix + m.preview;
    }
  });

  Live.on("read", (data) => {
    if (data.user_id !== me) {
      if (data.conversation_id === openId) {
        const reader = readers[String(data.user_id)];
        if (reader) reader.seq = Math.max(reader.seq, data.seq);
        drawSeen();
      }
      return;
    }
    const row = rowOf(data.conversation_id);
    if (row) setRowUnread(row, Math.max(0, Number(row.dataset.lastSeq || 0) - data.seq));
    if (data.conversation_id === openId) readSeq = Math.max(readSeq, data.seq);
  });

  Live.on("unread.total", (data) => setTotal(data.total));

  Live.on("conversation.new", (data) => {
    if (!list || rowOf(data.conversation_id)) return;
    const row = fromHTML(data.html);
    localize(row);
    list.prepend(row);
    const empty = document.querySelector("[data-chat-list-empty]");
    if (empty) empty.remove();
  });

  // A group was renamed.
  Live.on("conversation.updated", (data) => {
    const row = rowOf(data.conversation_id);
    if (row && data.title) row.querySelector(".chat-row-name").textContent = data.title;
    if (data.conversation_id === openId && data.title) {
      const heading = document.querySelector(".conversation-title h2");
      if (heading) heading.textContent = data.title;
    }
  });

  // I was removed, I left (in another tab), or the group was deleted: it leaves my list at once.
  Live.on("conversation.removed", (data) => {
    const row = rowOf(data.conversation_id);
    if (row) {
      if (!row.dataset.muted) setTotal(total - rowUnread(row));
      row.remove();
    }
    if (data.conversation_id === openId) {
      const words = { removed: "You were removed from this group.", deleted: "This group was deleted.",
        left: "You left this group." };
      if (typeof toast === "function") toast(words[data.why] || "This chat is no longer yours to see.", data.why !== "left");
      setTimeout(() => (location.href = "/chats"), 1800);
    }
  });

  // My role or mute changed, or a block opened or closed our DM: draw the chat again.
  Live.on("membership.updated", (data) => {
    if (data.conversation_id === openId) location.reload();
  });

  Live.on("reconnected", resync);

  Live.on("status", (status) => {
    if (!connectionNote) return;
    const words = {
      reconnecting: "Reconnecting... Messages you send now will go out when it's back.",
      "too-many": "You have too many tabs open. Close one, then reload this page.",
    };
    connectionNote.textContent = words[status] || "";
    connectionNote.hidden = !words[status];
  });

  document.addEventListener("visibilitychange", () => {
    if (reading()) markRead(lastSeq);
  });

  // ---------- Message tools: reply, edit, delete, react, "Seen by" ----------

  const csrfMeta = document.querySelector('meta[name="csrf-token"]');
  const csrfToken = csrfMeta ? csrfMeta.content : "";

  // Live messages come without a CSRF token in their forms (it would be the sender's): add ours.
  function addTokens(root) {
    root.querySelectorAll('form[method="post"]').forEach((form) => {
      if (form.querySelector('input[name="csrf_token"]')) return;
      const input = document.createElement("input");
      input.type = "hidden";
      input.name = "csrf_token";
      input.value = csrfToken;
      form.prepend(input);
    });
  }

  function markMyReactions(root) {
    root.querySelectorAll(".reaction[data-users]").forEach((chip) => {
      chip.classList.toggle("is-mine", chip.dataset.users.split(",").map(Number).includes(me));
    });
  }

  const readers = conversation && conversation.dataset.readers ? JSON.parse(conversation.dataset.readers) : {};
  const seenLine = document.querySelector("[data-seen-line]");

  // Under my last message: "Seen" in a DM, "Seen by Ana and Ben" or "Seen by everyone" in a group.
  function drawSeen() {
    if (!seenLine || !messages) return;
    const last = lastMessageEl();
    let text = "";
    if (last && !last.classList.contains("msg-system") && !last.classList.contains("is-pending")
        && Number(last.dataset.sender) === me) {
      const seq = Number(last.dataset.seq);
      const others = Object.entries(readers).filter(([id]) => Number(id) !== me);
      const seen = others.filter(([, reader]) => reader.seq >= seq).map(([, reader]) => reader.name);
      if (seen.length) {
        if (conversation.dataset.kind === "dm") text = "Seen";
        else if (seen.length === others.length && others.length > 1) text = "Seen by everyone";
        else if (seen.length === 1) text = `Seen by ${seen[0]}`;
        else text = `Seen by ${seen.slice(0, -1).join(", ")} and ${seen[seen.length - 1]}`;
      }
    }
    seenLine.textContent = text;
    seenLine.hidden = !text;
    messages.insertBefore(seenLine, messages.querySelector(".latest-anchor"));
  }

  const replyBanner = document.querySelector("[data-reply-banner]");
  const replyInput = document.querySelector("[data-reply-to]");

  function setReply(id, name, snippet) {
    if (!replyBanner || !replyInput) return;
    replyInput.value = id || "";
    if (id) {
      replyBanner.querySelector("[data-reply-name]").textContent = name || "";
      replyBanner.querySelector("[data-reply-snippet]").textContent = snippet || "";
    }
    replyBanner.hidden = !id;
  }

  const showError = (error) => {
    if (typeof toast === "function") toast((error && error.message) || "That didn't work. Please try again.", true);
  };

  const closeMenus = (root) => root.querySelectorAll("details[open]").forEach((menu) => (menu.open = false));

  function startEdit(li) {
    const textEl = li.querySelector("[data-text]");
    if (!textEl || li.querySelector(".edit-box")) return;
    const box = document.createElement("form");
    box.className = "edit-box";
    const field = document.createElement("textarea");
    field.value = textEl.textContent;
    field.maxLength = 4000;
    field.setAttribute("aria-label", "Edit your message");
    const actions = document.createElement("div");
    actions.className = "form-actions";
    const cancel = document.createElement("button");
    cancel.type = "button";
    cancel.className = "button button-quiet button-small";
    cancel.textContent = "Cancel";
    const save = document.createElement("button");
    save.type = "submit";
    save.className = "button button-small";
    save.textContent = "Save";
    actions.append(cancel, save);
    box.append(field, actions);
    textEl.hidden = true;
    textEl.after(box);
    field.focus();
    const stop = () => {
      box.remove();
      textEl.hidden = false;
    };
    cancel.addEventListener("click", stop);
    field.addEventListener("keydown", (event) => {
      if (event.key === "Escape") stop();
      if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
        event.preventDefault();
        box.requestSubmit();
      }
    });
    box.addEventListener("submit", (event) => {
      event.preventDefault();
      const body = field.value.trim();
      if (!body) return;
      save.disabled = true;
      Live.request("message.edit", { message_id: Number(li.dataset.id), body }, { timeout: 15000 })
        .catch((error) => {
          save.disabled = false;
          showError(error);
        }); // message.updated draws the edited message for everyone, this tab too
    });
  }

  function setUpTools() {
    if (!messages) return;
    // Forms in a message (reactions, delete): over the socket instead of a page load. The
    // delete form asks first (app.js); its first submit is stopped there, the approved one comes here.
    document.addEventListener("submit", (event) => {
      const form = event.target;
      if (event.defaultPrevented || !form.dataset || !form.dataset.tool || !messages.contains(form)) return;
      event.preventDefault();
      const li = form.closest(".msg");
      closeMenus(li);
      const id = Number(li.dataset.id);
      if (form.dataset.tool === "react") {
        const emoji = form.querySelector('input[name="emoji"]').value;
        Live.request("message.react", { message_id: id, emoji }, { timeout: 15000 }).catch(showError);
      } else if (form.dataset.tool === "delete") {
        Live.request("message.delete", { message_id: id }, { timeout: 15000 }).catch(showError);
      }
    });
    messages.addEventListener("click", (event) => {
      const tool = event.target.closest("a[data-tool]");
      if (tool) {
        event.preventDefault();
        const li = tool.closest(".msg");
        closeMenus(li);
        if (tool.dataset.tool === "reply") {
          setReply(li.dataset.id, tool.dataset.replyName, tool.dataset.replySnippet);
          const area = document.querySelector("[data-composer-text]");
          if (area) area.focus();
        } else if (tool.dataset.tool === "edit") {
          startEdit(li);
        }
        return;
      }
      const quote = event.target.closest("[data-jump]");
      if (quote) {
        const target = document.getElementById(`m-${quote.dataset.jump}`);
        if (!target) return; // an older message: the link loads it
        event.preventDefault();
        target.scrollIntoView({ block: "center", behavior: "smooth" });
        target.classList.add("is-flash");
        setTimeout(() => target.classList.remove("is-flash"), 1600);
      }
    });
    const cancel = document.querySelector("[data-reply-cancel]");
    if (cancel) {
      cancel.addEventListener("click", (event) => {
        event.preventDefault();
        setReply(null);
      });
    }
  }

  // ---------- Find and notify: mentions, muted chats, notifications ----------

  const myHandle = (body.dataset.handle || "").toLowerCase();
  function markMentions(root) {
    root.querySelectorAll(".mention[data-mention]").forEach((mention) => {
      mention.classList.toggle("is-me", mention.dataset.mention === myHandle);
    });
  }

  // The server decided I should hear about this message (not mine, chat not muted). Show a
  // notification if I'm not already looking at that chat.
  Live.on("notify", (data) => {
    const looking = data.conversation_id === openId && document.visibilityState === "visible";
    if (looking || !("Notification" in window) || Notification.permission !== "granted") return;
    try {
      const notice = new Notification(data.title, { body: data.body, tag: `chat-${data.conversation_id}`,
        icon: "/static/icons/icon-192.png" });
      notice.onclick = () => {
        window.focus();
        location.href = data.url;
      };
    } catch (error) {
      // Some phones only allow notifications from the service worker: the push covers them.
    }
  });

  Live.on("mention", (data) => {
    const row = rowOf(data.conversation_id);
    if (!row || (data.conversation_id === openId && reading())) return;
    row.classList.add("has-mention");
    const badge = row.querySelector("[data-mention-badge]");
    if (badge) badge.hidden = false;
  });

  // Reading a chat clears its @ too.
  Live.on("read", (data) => {
    if (data.user_id !== me) return;
    const row = rowOf(data.conversation_id);
    if (row && Number(row.dataset.lastSeq || 0) <= data.seq) {
      row.classList.remove("has-mention");
      const badge = row.querySelector("[data-mention-badge]");
      if (badge) badge.hidden = true;
    }
  });

  Live.on("chat.muted", (data) => {
    const row = rowOf(data.conversation_id);
    if (!row) return;
    row.classList.toggle("is-muted", data.muted);
    if (data.muted) row.dataset.muted = "1";
    else delete row.dataset.muted;
  });

  // "@" in a group's composer: suggest the members, by name or handle.
  function setUpMentionSuggestions() {
    const list = document.querySelector("[data-mention-list]");
    const area = document.querySelector("[data-composer-text]");
    const people = conversation && conversation.dataset.people ? JSON.parse(conversation.dataset.people) : [];
    if (!list || !area || !people.length) return;
    let choices = [];
    let chosen = 0;
    const close = () => {
      list.hidden = true;
      choices = [];
    };
    const wordAtCaret = () => {
      const before = area.value.slice(0, area.selectionStart);
      const match = before.match(/(^|\s)@([a-z0-9_]{0,20})$/i);
      return match ? match[2].toLowerCase() : null;
    };
    const draw = () => {
      list.replaceChildren(...choices.map((person, index) => {
        const item = document.createElement("li");
        item.setAttribute("role", "option");
        item.setAttribute("aria-selected", index === chosen ? "true" : "false");
        item.textContent = person.name;
        const handle = document.createElement("small");
        handle.textContent = `@${person.handle}`;
        item.append(handle);
        item.addEventListener("mousedown", (event) => {
          event.preventDefault();
          pick(person);
        });
        return item;
      }));
      list.hidden = !choices.length;
    };
    const pick = (person) => {
      const caret = area.selectionStart;
      const before = area.value.slice(0, caret).replace(/@([a-z0-9_]*)$/i, `@${person.handle} `);
      area.value = before + area.value.slice(caret);
      area.selectionStart = area.selectionEnd = before.length;
      close();
      area.focus();
    };
    area.addEventListener("input", () => {
      const word = wordAtCaret();
      if (word === null) return close();
      choices = people.filter((person) => person.handle.toLowerCase().startsWith(word)
        || person.name.toLowerCase().split(" ").some((part) => part.startsWith(word))).slice(0, 6);
      chosen = 0;
      draw();
    });
    area.addEventListener("keydown", (event) => {
      if (list.hidden) return;
      if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        event.preventDefault();
        chosen = (chosen + (event.key === "ArrowDown" ? 1 : choices.length - 1)) % choices.length;
        draw();
      } else if (event.key === "Enter" || event.key === "Tab") {
        event.preventDefault();
        event.stopImmediatePropagation(); // Enter picks a name here, it doesn't send
        pick(choices[chosen]);
      } else if (event.key === "Escape") {
        close();
      }
    }, true);
    area.addEventListener("blur", () => setTimeout(close, 150));
  }

  // Settings: "Turn on notifications". Asks the browser, then (when the server has VAPID keys)
  // subscribes this device to web push and sends the subscription to the server.
  function setUpNotifyButton() {
    const button = document.querySelector("[data-notify-on]");
    if (!button) return;
    const note = document.querySelector("[data-notify-note]");
    const say = (text) => { if (note) note.textContent = text; };
    if (!("Notification" in window)) {
      button.hidden = true;
      say("This browser can't show notifications.");
      return;
    }
    if (Notification.permission === "granted" && !button.dataset.key) button.textContent = "Notifications are on";
    button.addEventListener("click", async () => {
      try {
        if ((await Notification.requestPermission()) !== "granted") {
          say("Notifications are blocked for this site. Allow them in the browser's settings.");
          return;
        }
        const key = button.dataset.key;
        if (!key || !("serviceWorker" in navigator) || !("PushManager" in window)) {
          button.textContent = "Notifications are on";
          say("You'll get notifications while Paper Plane is open in a tab.");
          return;
        }
        const raw = Uint8Array.from(atob(key.replace(/-/g, "+").replace(/_/g, "/") + "=".repeat((4 - (key.length % 4)) % 4)),
          (c) => c.charCodeAt(0));
        const worker = await navigator.serviceWorker.ready;
        const subscription = await worker.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: raw });
        const response = await fetch(button.dataset.url, {
          method: "POST",
          headers: { "Content-Type": "application/json", "X-CSRF-Token": csrfToken },
          body: JSON.stringify(subscription),
        });
        button.textContent = "Notifications are on";
        say(response.ok ? "This device gets notifications, even when no tab is open." : "That didn't work. Please try again.");
      } catch (error) {
        say("This browser couldn't turn on notifications.");
      }
    });
  }

  // ---------- Presence: the dots, and the line under a DM's name ----------

  const statuses = new Map();

  function agoWords(iso) {
    const seconds = Math.max(0, (Date.now() - new Date(iso)) / 1000);
    if (seconds < 60) return "just now";
    if (seconds < 3600) return `${Math.floor(seconds / 60)} min ago`;
    if (seconds < 86400) {
      const hours = Math.floor(seconds / 3600);
      return `${hours} hour${hours === 1 ? "" : "s"} ago`;
    }
    const label = dayLabel(new Date(iso));
    return label === "Yesterday" ? "yesterday" : `on ${label}`;
  }

  function showPresence(userId, status, lastSeen) {
    statuses.set(userId, status);
    document.querySelectorAll(`[data-presence-dot="${userId}"]`).forEach((dot) => {
      dot.className = `dot dot-${status}`;
    });
    document.querySelectorAll(`[data-status-line][data-user="${userId}"]`).forEach((line) => {
      if (lastSeen) line.dataset.lastSeen = lastSeen;
      else if (status === "offline" && lastSeen === null) delete line.dataset.lastSeen;
      if (status === "online") line.textContent = "Online";
      else if (status === "away") line.textContent = "Away";
      else line.textContent = line.dataset.lastSeen ? `Last seen ${agoWords(line.dataset.lastSeen)}` : `@${line.dataset.handle}`;
    });
  }

  Live.on("presence", (data) => showPresence(data.user_id, data.status, data.last_seen));
  Live.on("presence.snapshot", (data) => {
    Object.entries(data.statuses || {}).forEach(([userId, status]) => showPresence(Number(userId), status, undefined));
  });

  // My tab hidden or shown: after 5 minutes with every tab hidden, friends see "Away".
  const tellVisibility = () => Live.tell("presence", { visible: document.visibilityState === "visible" });
  document.addEventListener("visibilitychange", tellVisibility);
  Live.on("status", (status) => {
    if (status === "online" && document.visibilityState !== "visible") tellVisibility();
  });

  // "Last seen 3 min ago" keeps up with the clock.
  setInterval(() => {
    document.querySelectorAll("[data-status-line][data-last-seen]").forEach((line) => {
      const status = statuses.get(Number(line.dataset.user));
      if (!status || status === "offline") line.textContent = `Last seen ${agoWords(line.dataset.lastSeen)}`;
    });
  }, 30000);

  // ---------- Typing ----------

  const typingLine = document.querySelector("[data-typing]");
  const typists = new Map(); // user id -> { name, timer }

  function drawTyping() {
    if (!typingLine) return;
    const names = Array.from(typists.values(), (typist) => typist.name);
    if (names.length === 0) typingLine.textContent = "";
    else if (names.length === 1) typingLine.textContent = `${names[0]} is typing...`;
    else if (names.length === 2) typingLine.textContent = `${names[0]} and ${names[1]} are typing...`;
    else typingLine.textContent = "Several people are typing...";
  }

  function stopTyping(userId) {
    const typist = typists.get(userId);
    if (!typist) return;
    clearTimeout(typist.timer);
    typists.delete(userId);
    drawTyping();
  }

  Live.on("typing", (data) => {
    if (data.conversation_id !== openId || data.user_id === me) return;
    stopTyping(data.user_id);
    typists.set(data.user_id, { name: data.name, timer: setTimeout(() => stopTyping(data.user_id), 5000) });
    drawTyping();
  });

  // Their message arrived: they've stopped typing.
  document.addEventListener("paperplane:message", (event) => stopTyping(event.detail.sender_id));

  function setUpTypingSignal() {
    const area = document.querySelector("[data-composer-text]");
    if (!area || !openId) return;
    let lastSignal = 0;
    area.addEventListener("input", () => {
      const now = Date.now();
      if (area.value.trim() && now - lastSignal > 3000) {
        lastSignal = now;
        Live.tell("typing", { conversation_id: openId });
      }
    });
    document.addEventListener("paperplane:sent", () => (lastSignal = 0));
  }

  // ---------- Start ----------

  document.addEventListener("DOMContentLoaded", () => {
    if (messages) localize(messages);
    setUpComposer();
    setUpTypingSignal();
    setUpTools();
    setUpMentionSuggestions();
    setUpNotifyButton();
    if (messages) {
      markMyReactions(messages);
      markMentions(messages);
    }
    if (scroller) {
      scroller.addEventListener("scroll", () => {
        if (reading()) markRead(lastSeq);
      }, { passive: true });
    }
    setTotal(total);
    Live.start();
  });

  window.PaperPlane = { me, openId, rowOf, setRowUnread, setTotal, markRead, resync };
})();
