import { createContext, useContext } from "react";

/**
 * Is the canvas display only? Edges and nodes lie deep in React Flow and get no props of
 * their own passed through; over this context they still know whether they may offer
 * editing (editor) or not (the runtime view of an instance).
 */
const CanvasReadOnly = createContext(false);

export const CanvasModeProvider = CanvasReadOnly.Provider;

export function useCanvasReadOnly(): boolean {
  return useContext(CanvasReadOnly);
}
