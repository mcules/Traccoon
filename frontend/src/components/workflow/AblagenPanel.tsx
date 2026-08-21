import { useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "../../api";
import { formatDateTime } from "../../lib/formatTime";
import { tr } from "../../i18n";
import { Area, Etikett, Listing, ListingLeer, ListenLine } from "../ui";

type Ablage = { id: number; key: string; name: string; description: string;
                keep: number; last_title: string; last_at: string | null; count: number | null };

/**
 * Ablagen: die Texte, die Abläufe geschrieben haben.
 *
 * Das Gegenstück zu den Messreihen — dort Zahlen mit Verlauf, hier Texte mit Verlauf. Beide
 * beantworten dieselbe Frage: Was ein Ablauf erarbeitet hat, soll ihn überdauern.
 */
export default function AblagenPanel() {
  const nav = useNavigate();
  const { data } = useQuery({ queryKey: ["ablagen"],
                              queryFn: () => api.get<Ablage[]>("/documents") });
  const ablagen = data || [];

  return (
    <Area hinweis={tr("ablagen.einleitung")}>
      <Listing>
        {ablagen.length === 0 && <ListingLeer>{tr("ablagen.keine")}</ListingLeer>}
        {ablagen.map((a) => (
          <ListenLine key={a.id} spalten="sm:grid-cols-[minmax(0,1fr)_10rem_auto]"
            onClick={() => nav(`/documents/${encodeURIComponent(a.key)}`)}>
            <div className="min-w-0">
              <div className="truncate font-medium text-ink">{a.name || a.key}</div>
              <div className="mt-0.5 flex items-center gap-2 text-xs text-muted">
                <span className="truncate font-mono">{a.key}</span>
                {a.last_title && <><span className="text-line">·</span>
                  <span className="truncate">{a.last_title}</span></>}
              </div>
            </div>
            <span className="text-xs text-muted">
              {a.last_at ? formatDateTime(a.last_at) : "—"}
            </span>
            <Etikett>{tr("ablagen.fassungen", { anzahl: String(a.count ?? 0) })}</Etikett>
          </ListenLine>
        ))}
      </Listing>
    </Area>
  );
}
