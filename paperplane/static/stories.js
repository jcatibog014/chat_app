// Stories, the live parts. Without this file the pages still work: the viewer's left and right
// thirds are links, and the circles are drawn by the server.
(() => {
  // ---------- The viewer: 5 seconds a story, then the next ----------
  const viewer = document.querySelector("[data-story-viewer]");
  if (viewer) {
    const now = viewer.querySelector(".story-bar.is-now");
    let left = 5000;
    let started = performance.now();
    let timer = null;
    let held = false;
    const go = () => { location.href = viewer.dataset.next; };
    const run = () => {
      if (held || timer) return;
      started = performance.now();
      timer = setTimeout(go, left);
      if (now) now.classList.remove("is-paused");
    };
    const pause = () => {
      held = true;
      if (timer) {
        clearTimeout(timer);
        timer = null;
        left = Math.max(300, left - (performance.now() - started));
      }
      if (now) now.classList.add("is-paused");
    };
    const resume = () => {
      held = false;
      run();
    };
    // Typing a reply, reading the Seen by list, or holding a finger down: wait.
    viewer.querySelectorAll("[data-story-hold], details").forEach((part) => {
      part.addEventListener("focusin", pause);
      part.addEventListener("focusout", () => setTimeout(() => {
        if (!part.contains(document.activeElement) && !part.open) resume();
      }));
      part.addEventListener("toggle", () => (part.open ? pause() : resume()));
    });
    const stage = viewer.querySelector(".story-face");
    if (stage) {
      stage.addEventListener("pointerdown", pause);
      stage.addEventListener("pointerup", resume);
      stage.addEventListener("pointerleave", () => held && resume());
    }
    document.addEventListener("keydown", (event) => {
      if (event.target.closest("input, textarea")) return;
      if (event.key === "ArrowRight") go();
      if (event.key === "ArrowLeft") {
        const prev = viewer.querySelector(".story-prev");
        if (prev) location.href = prev.href;
      }
      if (event.key === "Escape") location.href = "/stories";
    });
    document.addEventListener("visibilitychange", () => (document.hidden ? pause() : resume()));
    if (now) now.classList.add("is-running");
    run();
  }

  // ---------- Live: new stories, Seen by ----------
  if (typeof Live !== "undefined") {
    const refreshTray = async () => {
      const tray = document.querySelector("[data-story-tray]");
      if (!tray) return;
      try {
        const response = await fetch("/stories/tray", { headers: { Accept: "text/html" } });
        if (response.ok) tray.innerHTML = await response.text();
      } catch (error) {
        // Offline: the next event or page load will catch up.
      }
    };
    Live.on("story.new", refreshTray);
    Live.on("story.gone", refreshTray);
    Live.on("story.seen", (data) => {
      document.querySelectorAll(`[data-seen-count="${data.story_id}"]`).forEach((count) => {
        count.textContent = data.count;
      });
    });
    // On the story pages, a short note when someone reacts to one of my stories.
    Live.on("story.reacted", (data) => {
      const onStories = viewer || document.querySelector("[data-stories-page]");
      if (onStories && data.emoji && typeof toast === "function") toast(`${data.name} reacted ${data.emoji} to your story.`);
    });
  }

  // ---------- New Story: show the right part, and a preview ----------
  const form = document.querySelector("[data-story-form]");
  if (form) {
    const parts = form.querySelectorAll("[data-story-part]");
    const text = form.querySelector("[data-story-text]");
    const card = form.querySelector("[data-story-card-preview]");
    const label = form.querySelector("[data-story-text-label]");
    const show = () => {
      const kind = form.querySelector("[data-story-kind]:checked").value;
      parts.forEach((part) => (part.hidden = part.dataset.storyPart !== kind));
      text.required = kind === "text";
      text.placeholder = kind === "text" ? "What's new?" : "Add a caption...";
      label.innerHTML = kind === "text" ? "Text" : 'Caption <span class="hint">(optional)</span>';
    };
    form.querySelectorAll("[data-story-kind]").forEach((choice) => choice.addEventListener("change", show));
    show();
    form.querySelectorAll("[data-story-bg]").forEach((choice) => {
      choice.addEventListener("change", () => {
        card.className = card.className.replace(/story-bg-\w+/, `story-bg-${choice.value}`);
      });
    });
    text.addEventListener("input", () => {
      card.querySelector("p").textContent = text.value.trim() || "What's new?";
    });
    const photo = form.querySelector("[data-story-photo]");
    const preview = form.querySelector("[data-story-preview]");
    let url = null;
    const zone = form.querySelector("[data-dropzone]");
    photo.addEventListener("change", () => {
      if (url) URL.revokeObjectURL(url);
      const file = photo.files[0];
      url = file ? URL.createObjectURL(file) : null;
      preview.hidden = !url;
      if (zone) zone.classList.toggle("has-file", Boolean(url));
      if (url) preview.src = url;
    });
    // Drag a picture onto the box: it becomes the chosen file.
    if (zone) {
      ["dragenter", "dragover"].forEach((type) => zone.addEventListener(type, (event) => {
        event.preventDefault();
        zone.classList.add("is-over");
      }));
      ["dragleave", "drop"].forEach((type) => zone.addEventListener(type, () => zone.classList.remove("is-over")));
      zone.addEventListener("drop", (event) => {
        event.preventDefault();
        const files = event.dataTransfer && event.dataTransfer.files;
        if (!files || !files.length) return;
        const chosen = new DataTransfer();
        chosen.items.add(files[0]);
        photo.files = chosen.files;
        photo.dispatchEvent(new Event("change", { bubbles: true }));
      });
    }
  }

  // A reply to a story, seen by the story's owner: "Replied to your story".
  const me = Number(document.body.dataset.me);
  document.querySelectorAll("[data-story-owner]").forEach((quote) => {
    if (Number(quote.dataset.storyOwner) !== me) return;
    const words = quote.querySelector("[data-story-quote-words]");
    if (words) words.textContent = "Replied to your story";
  });
})();
