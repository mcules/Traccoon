/**
 * Asking for files without a visible file input.
 *
 * On a phone the two ways this app had for getting an image into a note —
 * paste and drag-and-drop — effectively do not exist. What does exist is the
 * camera and the gallery, and both are reachable through a file input with the
 * right `accept`/`capture`. The element is created, used and thrown away, so
 * nothing has to hold a hidden input in its markup.
 */

function pick(opts: { accept?: string; capture?: 'environment' | 'user'; multiple?: boolean }): Promise<File[]> {
  return new Promise((resolve) => {
    const input = document.createElement('input');
    input.type = 'file';
    if (opts.accept) input.accept = opts.accept;
    if (opts.capture) input.capture = opts.capture;
    input.multiple = !!opts.multiple;
    input.style.position = 'fixed';
    input.style.left = '-9999px';
    document.body.appendChild(input);
    let done = false;
    const finish = (files: File[]) => {
      if (done) return;
      done = true;
      input.remove();
      resolve(files);
    };
    input.addEventListener('change', () => finish(Array.from(input.files ?? [])));
    // A cancelled picker fires nothing on older browsers; the window regaining
    // focus is the only hint that the user backed out.
    window.addEventListener('focus', () => window.setTimeout(() => finish([]), 800), { once: true });
    input.click();
  });
}

export const pickCameraPhoto = () => pick({ accept: 'image/*', capture: 'environment' });
export const pickFromGallery = () => pick({ accept: 'image/*,video/*', multiple: true });
export const pickAnyFile = () => pick({ multiple: true });
