import { useAssistantOffer } from '../../assistant/context';
import { tr } from '../../i18n';
import { getActiveEditor } from '../lib/activeEditor';
import { useStore } from '../lib/store';

/**
 * Was die Notizansicht dem Assistenten mitgeben kann.
 *
 * Steht als eigener, unsichtbarer Baustein da und nicht im Panel: das Panel
 * haengt jetzt im ganzen Haus, und was auf dieser Seite zu sehen ist, weiss
 * nur diese Seite. Sie meldet es an, solange sie offen ist.
 *
 * Markiert jemand etwas, ist das Markierte gemeint und nicht die ganze Notiz —
 * eine Frage zu drei Zeilen soll nicht vierzigtausend Zeichen mitschleppen.
 */
export default function NoteOffer() {
  const activePath = useStore((s) => s.activePath);
  useAssistantOffer(activePath ? [{
    key: 'note',
    rank: 20,
    label: tr('notes_assistant.send_note'),
    get: () => {
      const view = getActiveEditor();
      const sel = view?.state.selection.main;
      const marked = view && sel && !sel.empty ? view.state.sliceDoc(sel.from, sel.to) : '';
      return marked
        ? tr('assistant.marked_in_note', { path: activePath, text: marked })
        : tr('assistant.i_see_note', { path: activePath });
    },
  }] : []);
  return null;
}
