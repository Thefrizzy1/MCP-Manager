/** Copy text to the clipboard. Returns whether it worked.
 *
 *  `navigator.clipboard` only exists on secure pages (https or localhost). The
 *  dashboard is usually opened over plain http on a home network or a tailnet,
 *  where it is undefined, so fall back to selecting the text in a hidden field
 *  and using the browser's copy command, which works everywhere. */
export async function copyText(text: string): Promise<boolean> {
  if (window.isSecureContext && navigator.clipboard) {
    try {
      await navigator.clipboard.writeText(text)
      return true
    } catch {
      /* permission denied: try the fallback */
    }
  }
  const el = document.createElement('textarea')
  el.value = text
  el.setAttribute('readonly', '')
  el.style.position = 'absolute'
  el.style.left = '-9999px'
  document.body.appendChild(el)
  el.select()
  el.setSelectionRange(0, text.length)
  let ok = false
  try {
    ok = document.execCommand('copy')
  } catch {
    ok = false
  }
  document.body.removeChild(el)
  return ok
}
