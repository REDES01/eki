// The knowledge card's own part in an open card (projectcardpanel.js draws it under "Files").
// Registered in window.projectCardKinds; draws nothing yet.
(function () {
  window.projectCardKinds = window.projectCardKinds || {};
  window.projectCardKinds["knowledge"] = {
    title: "Files",
    draw(el, card, pid, ro) {},
    close() {},
  };
  if (window.projectCardPanel && projectCardPanel.redraw) projectCardPanel.redraw();
})();
