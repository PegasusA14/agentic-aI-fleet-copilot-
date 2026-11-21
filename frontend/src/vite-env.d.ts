/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** "fixtures" serves the stored real answers with no backend; anything else talks to the API. */
  readonly VITE_FLEET_DATA?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
