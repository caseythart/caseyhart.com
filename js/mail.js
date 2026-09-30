// Assembles mailto links at page load so the address is not in the raw HTML.
// Usage: <a data-mail-user="hello" data-mail-domain="caseyhart.com" data-mail-subject="Speaking inquiry">Email me</a>
// If the link has no text, the address itself is shown.
document.querySelectorAll("[data-mail-user]").forEach(function (el) {
  var addr = el.dataset.mailUser + "@" + el.dataset.mailDomain;
  var subject = el.dataset.mailSubject;
  el.href = "mailto:" + addr + (subject ? "?subject=" + encodeURIComponent(subject) : "");
  if (!el.textContent.trim()) el.textContent = addr;
});
