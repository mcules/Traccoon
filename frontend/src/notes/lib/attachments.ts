import { api } from './api';
import { useStore } from './store';
import { fmtInsert } from './activeEditor';

/**
 * Put files into the vault and reference them from the note being written.
 *
 * The server decides where they land (the vault's attachment setting), and the
 * link goes in at the caret: an image as an embed, anything else as a plain
 * link — which is what the predecessor does, and what makes a PDF or an audio file
 * usable rather than broken.
 */
export async function attachFiles(files: File[]): Promise<void> {
  const { activePath, notify, setContent, content } = useStore.getState();
  for (const file of files) {
    try {
      const { path } = await api.upload(file, { note: activePath ?? '' });
      const link = `${file.type.startsWith('image/') ? '!' : ''}[[${path}]]`;
      if (!fmtInsert(link)) setContent(`${content}\n${link}\n`);
      notify(`Eingefügt: ${path}`);
    } catch (e: any) {
      notify(e.message);
    }
  }
}

/** Rename a clipboard image to the moment it was pasted, as the predecessor does. */
export function nameClipboardImages(files: File[], now = new Date()): File[] {
  const p2 = (n: number) => String(n).padStart(2, '0');
  return files.map((f) => {
    if (!f.type.startsWith('image/') || !/^image\.\w+$/i.test(f.name)) return f;
    const ext = f.name.slice(f.name.lastIndexOf('.'));
    const name =
      `Pasted image ${now.getFullYear()}${p2(now.getMonth() + 1)}${p2(now.getDate())}` +
      `${p2(now.getHours())}${p2(now.getMinutes())}${p2(now.getSeconds())}${ext}`;
    return new File([f], name, { type: f.type });
  });
}
