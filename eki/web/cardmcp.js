// The mcp card's own part in an open card (projectcardpanel.js draws it under "Server").
// Registered in window.projectCardKinds; draws nothing yet.
(function () {
  window.projectCardKinds = window.projectCardKinds || {};
  window.projectCardKinds["mcp"] = {
    title: "Server",
    draw(el, card, pid, ro) {},
    close() {},
  };
  if (window.projectCardPanel && projectCardPanel.redraw) projectCardPanel.redraw();
})();
