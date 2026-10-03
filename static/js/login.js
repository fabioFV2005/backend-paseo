/* Login page: loading feedback on the Google button.
   The flow is a plain redirect, so this only covers the wait between the
   click and the navigation to accounts.google.com. Without JS the link
   still works; the loading state simply never appears. */
(function () {
  var button = document.querySelector(".btn-google");
  if (!button) return;

  var label = button.querySelector(".btn-google-label");

  button.addEventListener("click", function () {
    if (button.classList.contains("is-loading")) return;
    button.classList.add("is-loading");
    button.setAttribute("aria-busy", "true");
    label.textContent = button.dataset.loadingLabel;
  });

  // Returning with the browser back button (bfcache) must reset the button,
  // otherwise it looks stuck on "Redirecting…" after a cancellation.
  window.addEventListener("pageshow", function (event) {
    if (!event.persisted) return;
    button.classList.remove("is-loading");
    button.removeAttribute("aria-busy");
    label.textContent = button.dataset.defaultLabel;
  });
})();
