import { createContext, useContext } from "react";

/**
 * Ist die Fläche nur Anzeige? Kanten und Knoten liegen tief in React Flow und bekommen
 * keine eigenen Props durchgereicht — über diesen Kontext wissen sie trotzdem, ob sie
 * Bearbeiten anbieten dürfen (Editor) oder nicht (Laufzeit-Ansicht einer Instanz).
 */
const CanvasReadOnly = createContext(false);

export const CanvasModeProvider = CanvasReadOnly.Provider;

export function useCanvasReadOnly(): boolean {
  return useContext(CanvasReadOnly);
}
