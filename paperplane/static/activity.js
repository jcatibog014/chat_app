// Notifications: the number beside Notifications in the menu and the bottom bar changes live
// ("activity.count" from activity.py). On the Notifications page itself, a button offers the
// new ones (the page doesn't jump around while you read it).
(() => {
  if (typeof Live === "undefined") return;
  const refresh = document.querySelector("[data-activity-refresh]");
  Live.on("activity.count", (data) => {
    const unread = Number(data.unread) || 0;
    document.querySelectorAll("[data-activity-unread]").forEach((badge) => {
      badge.textContent = unread;
      badge.hidden = unread === 0;
    });
    if (refresh && unread > 0) refresh.hidden = false;
  });
})();
