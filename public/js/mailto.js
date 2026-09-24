// Shared "send a structured report by email" helper.
//
// This app has no backend mail server, so every place that needs to collect
// structured information from the user (the in-app Feedback button in
// viewer.html/viewport_selector.html, the landing page's account-request
// form) does it the same way: build a pre-filled mailto: link and navigate
// to it, leaving the actual send to the visitor's own mail client. Plain
// script (not a module) so it's a global, callable from the same inline
// onclick="..." handlers the pages already use for this.
function openMailtoReport(to, subject, body) {
    window.location.href = `mailto:${to}?subject=${encodeURIComponent(subject)}&body=${encodeURIComponent(body)}`;
}
