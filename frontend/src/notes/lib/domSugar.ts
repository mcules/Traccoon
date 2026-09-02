/**
 * the predecessor's DOM sugar (`el.createEl`, `createDiv`, `setText`, `addClass`, …).
 *
 * The predecessor adds these to HTMLElement.prototype, and both community plugins and
 * ```dataviewjs snippets use them as if they were standard DOM. Installed once
 * at startup so a plugin calling `document.body.createDiv()` does not die on
 * its first line.
 */

interface ElOpts {
  text?: string;
  cls?: string | string[];
  attr?: Record<string, string | number | boolean>;
  href?: string;
  title?: string;
  type?: string;
  value?: string;
  placeholder?: string;
}

let domPatched = false;
export function installDomSugar(): void {
  if (domPatched) return;
  domPatched = true;
  const proto = HTMLElement.prototype as unknown as Record<string, unknown>;
  const define = (name: string, fn: (...args: never[]) => unknown) => {
    if (!(name in proto)) Object.defineProperty(proto, name, { value: fn, writable: true, configurable: true });
  };
  function applyOpts(el: HTMLElement, o?: ElOpts | string): HTMLElement {
    if (typeof o === 'string') {
      el.className = o;
      return el;
    }
    if (!o) return el;
    if (o.text !== undefined) el.textContent = String(o.text);
    if (o.cls) el.className = Array.isArray(o.cls) ? o.cls.join(' ') : o.cls;
    if (o.href !== undefined) el.setAttribute('href', o.href);
    if (o.title !== undefined) el.setAttribute('title', o.title);
    if (o.type !== undefined) el.setAttribute('type', o.type);
    if (o.value !== undefined) el.setAttribute('value', o.value);
    if (o.placeholder !== undefined) el.setAttribute('placeholder', o.placeholder);
    if (o.attr) for (const [k, v] of Object.entries(o.attr)) el.setAttribute(k, String(v));
    return el;
  }
  define('createEl', function (this: HTMLElement, tag: string, o?: ElOpts | string) {
    const el = document.createElement(tag);
    applyOpts(el, o);
    this.appendChild(el);
    return el;
  } as never);
  define('createDiv', function (this: HTMLElement, o?: ElOpts | string) {
    return (this as unknown as { createEl: (t: string, o?: ElOpts | string) => HTMLElement }).createEl('div', o);
  } as never);
  define('createSpan', function (this: HTMLElement, o?: ElOpts | string) {
    return (this as unknown as { createEl: (t: string, o?: ElOpts | string) => HTMLElement }).createEl('span', o);
  } as never);
  define('setText', function (this: HTMLElement, t: string) {
    this.textContent = t;
    return this;
  } as never);
  define('appendText', function (this: HTMLElement, t: string) {
    this.appendChild(document.createTextNode(t));
    return this;
  } as never);
  define('empty', function (this: HTMLElement) {
    this.textContent = '';
    return this;
  } as never);
  define('addClass', function (this: HTMLElement, ...c: string[]) {
    this.classList.add(...c);
    return this;
  } as never);
  define('removeClass', function (this: HTMLElement, ...c: string[]) {
    this.classList.remove(...c);
    return this;
  } as never);
  define('toggleClass', function (this: HTMLElement, c: string, on?: boolean) {
    this.classList.toggle(c, on);
    return this;
  } as never);
  define('setAttr', function (this: HTMLElement, k: string, v: string) {
    this.setAttribute(k, v);
    return this;
  } as never);
}

