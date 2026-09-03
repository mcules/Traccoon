import { useStore } from '../lib/store';
import { tr } from "../../i18n";
import { attachFiles } from '../lib/attachments';
import { pickAnyFile, pickCameraPhoto, pickFromGallery } from '../lib/filePick';
import Icon from './Icon';

/**
 * The three ways a file gets into a note on a phone: take a picture, pick from
 * the gallery, or choose any file. Paste and drag-and-drop cover the desktop
 * and neither exists on touch, which left the phone with no way at all.
 */
export default function AttachSheet() {
  const open = useStore((s) => s.attachSheetOpen);
  const setOpen = useStore((s) => s.setAttachSheet);
  if (!open) return null;

  const run = async (pick: () => Promise<File[]>) => {
    setOpen(false);
    const files = await pick();
    if (files.length) await attachFiles(files);
  };

  return (
    <div className="modal-bg" onClick={() => setOpen(false)}>
      <div className="sheet" onClick={(e) => e.stopPropagation()}>
        <div className="sheet-item" onClick={() => void run(pickCameraPhoto)}>
          <Icon name="camera" size={18} /> {tr("notes_attach.take_a_photo")}
        </div>
        <div className="sheet-item" onClick={() => void run(pickFromGallery)}>
          <Icon name="image" size={18} /> {tr("notes_attach.from_the_gallery")}
        </div>
        <div className="sheet-item" onClick={() => void run(pickAnyFile)}>
          <Icon name="paperclip" size={18} /> {tr("notes_attach.choose_a_file")}
        </div>
      </div>
    </div>
  );
}
