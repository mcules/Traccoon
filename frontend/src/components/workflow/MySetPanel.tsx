import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, api, workflowApi } from "../../api";
import type { WorkflowSet } from "./types";

interface Me {
  id: number;
  workflow_set_id?: number | null;
}

/**
 * Persönlicher Prozess-Satz: gilt für alle Projekte, in denen ich die Owner-Rolle habe und
 * die nichts Eigenes gewählt haben. Angelegt wird er als vollständige Kopie des
 * ausgelieferten Standards — danach ist er unabhängig.
 */
export default function MySetPanel() {
  const qc = useQueryClient();
  const nav = useNavigate();
  const [err, setErr] = useState("");
  const [name, setName] = useState("");

  const { data: me } = useQuery({ queryKey: ["me"], queryFn: () => api.get<Me>("/auth/me") });
  const { data: sets } = useQuery({ queryKey: ["workflow-sets"], queryFn: workflowApi.sets });
  const meiner: WorkflowSet | undefined = sets?.find((s) => s.id === me?.workflow_set_id);

  const { data: slots } = useQuery({
    queryKey: ["workflow-set-slots", meiner?.id],
    queryFn: () => workflowApi.setSlots(meiner!.id),
    enabled: !!meiner,
  });

  const inv = () => {
    qc.invalidateQueries({ queryKey: ["me"] });
    qc.invalidateQueries({ queryKey: ["workflow-sets"] });
  };
  const fail = (e: unknown) => setErr(e instanceof ApiError ? e.message : "Fehler");

  const anlegen = useMutation({
    mutationFn: () => workflowApi.createMySet({ name: name.trim() || undefined }),
    onSuccess: () => {
      setErr("");
      setName("");
      inv();
    },
    onError: fail,
  });
  const aufgeben = useMutation({
    mutationFn: () => workflowApi.dropMySet(),
    onSuccess: () => {
      setErr("");
      inv();
    },
    onError: fail,
  });

  return (
    <div className="space-y-3 rounded-lg border border-line bg-card p-4">
      <p className="text-sm text-muted">
        <b>Meine Standard-Prozesse</b> — Ticket-Lebenszyklus, Abnahme, Beschaffung und
        Ticket-Eingang für <b>alle Projekte, in denen ich Eigentümer bin</b>. Ohne eigenen Satz
        gilt der ausgelieferte Traccoon-Standard. Ein einzelnes Projekt kann jederzeit davon
        abweichen (Projekt → Prozesse → Anpassen).
      </p>

      {err && <div className="rounded border border-red-500/40 bg-red-500/10 p-2 text-sm text-red-300">{err}</div>}

      {!meiner ? (
        <div className="flex flex-wrap items-center gap-2">
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Name (optional)"
            className="rounded border border-line bg-surface px-2 py-1.5 text-sm text-ink"
          />
          <button
            onClick={() => anlegen.mutate()}
            disabled={anlegen.isPending}
            className="rounded bg-brand px-3 py-1.5 text-sm text-white disabled:opacity-50"
          >
            Eigenen Satz anlegen (Kopie des Standards)
          </button>
        </div>
      ) : (
        <>
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-medium">{meiner.name}</span>
            <span className="rounded bg-violet-500/15 px-1.5 py-0.5 text-xs text-violet-300">aktiv</span>
            <div className="flex-1" />
            <button
              onClick={() => {
                if (confirm("Eigenen Satz aufgeben? Meine Projekte folgen dann wieder dem Standard.")) {
                  aufgeben.mutate();
                }
              }}
              className="rounded border border-line px-2 py-1 text-xs hover:border-red-400"
            >
              Aufgeben
            </button>
          </div>
          <div className="space-y-1">
            {slots?.map((s) => (
              <div
                key={s.slot}
                className="flex items-center gap-2 rounded border border-line bg-surface px-2 py-1.5 text-sm"
              >
                <span>{s.name}</span>
                {!s.published && <span className="text-xs text-yellow-400">Entwurf</span>}
                <div className="flex-1" />
                {s.definition_id && (
                  <button
                    onClick={() => nav(`/workflows/${s.definition_id}`)}
                    className="rounded border border-line px-2 py-0.5 text-xs hover:border-brand"
                  >
                    Bearbeiten
                  </button>
                )}
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
