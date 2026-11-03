// Sweep harness: fixtures record what happened to them in a data attribute on
// <html>. `eval` runs in an isolated world that cannot see page globals, but it
// can read the DOM, so the log lives there (and stays out of the aria tree).
function log(msg) {
  const root = document.documentElement;
  root.dataset.log = (root.dataset.log ? root.dataset.log + ' ' : '') + msg;
}
document.addEventListener('click', e => log('click:' + (e.target.id || e.target.tagName)), true);
document.addEventListener('dblclick', e => log('dblclick:' + (e.target.id || e.target.tagName)), true);
