/* Desktop notifications + title badge for long unattended runs. */

const BASE_TITLE = 'DOINg.AI SuperCode';
let attention = 0;

export function requestNotifyPermission(): void {
  try {
    if ('Notification' in window && Notification.permission === 'default') Notification.requestPermission().catch(() => {});
  } catch {
    /* unsupported */
  }
}

export function notify(title: string, body: string): void {
  if (document.visibilityState === 'visible' && document.hasFocus()) return;
  attention += 1;
  document.title = `(${attention}) ${BASE_TITLE}`;
  try {
    if ('Notification' in window && Notification.permission === 'granted') {
      const n = new Notification(title, { body: body.slice(0, 180), tag: 'doing-ai-supercode' });
      n.onclick = () => {
        window.focus();
        n.close();
      };
    }
  } catch {
    /* unsupported */
  }
}

export function clearAttention(): void {
  attention = 0;
  document.title = BASE_TITLE;
}
