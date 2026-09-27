import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from 'react';

/** Global overlays that any screen can open: the ⌘K palette, a learning deck, the update composer. */
interface UiState {
  paletteOpen: boolean;
  openPalette: () => void;
  closePalette: () => void;
  learnCourseId: string | null;
  openLearn: (courseId: string) => void;
  closeLearn: () => void;
  composeOpen: boolean;
  openCompose: () => void;
  closeCompose: () => void;
}

const Ctx = createContext<UiState | null>(null);

export function UiProvider({ children }: { children: ReactNode }) {
  const [paletteOpen, setPalette] = useState(false);
  const [learnCourseId, setLearn] = useState<string | null>(null);
  const [composeOpen, setCompose] = useState(false);
  const openPalette = useCallback(() => setPalette(true), []);
  const closePalette = useCallback(() => setPalette(false), []);
  const openLearn = useCallback((id: string) => setLearn(id), []);
  const closeLearn = useCallback(() => setLearn(null), []);
  const openCompose = useCallback(() => setCompose(true), []);
  const closeCompose = useCallback(() => setCompose(false), []);
  const value = useMemo(
    () => ({
      paletteOpen,
      openPalette,
      closePalette,
      learnCourseId,
      openLearn,
      closeLearn,
      composeOpen,
      openCompose,
      closeCompose,
    }),
    [
      paletteOpen,
      openPalette,
      closePalette,
      learnCourseId,
      openLearn,
      closeLearn,
      composeOpen,
      openCompose,
      closeCompose,
    ],
  );
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useUi(): UiState {
  const v = useContext(Ctx);
  if (!v) throw new Error('useUi outside UiProvider');
  return v;
}
