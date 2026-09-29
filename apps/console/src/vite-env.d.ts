/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** "true" compiles the development sign-in picker into a production bundle (local compose stack only). */
  readonly VITE_DEMO_SIGNIN?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
