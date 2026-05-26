export interface SharePointActionConfigView {
  id: number | null;
  is_enabled: boolean;
  cc_pair_id: number | null;
  write_scopes_available: boolean;
  detected_roles: string[];
  last_scope_check_at: string | null;
  allow_download_when_sp_available: boolean;
  template_sp_drive_id: string | null;
  template_sp_item_id: string | null;
}

export interface SharePointActionConfigUpdate {
  is_enabled?: boolean;
  cc_pair_id?: number | null;
  allow_download_when_sp_available?: boolean;
  template_sp_drive_id?: string | null;
  template_sp_item_id?: string | null;
  clear_template?: boolean;
}

export interface ScopeDetectionResponse {
  has_write: boolean;
  detected_roles: string[];
  missing_scopes: string[];
}

export interface SharePointCcPairOption {
  cc_pair_id: number;
  name: string;
}

const ROOT = "/api/admin/word-action";

export async function fetchConfig(): Promise<SharePointActionConfigView> {
  const r = await fetch(ROOT);
  if (!r.ok) throw new Error(`GET ${ROOT} -> ${r.status}`);
  return r.json();
}

export async function updateConfig(
  payload: SharePointActionConfigUpdate
): Promise<SharePointActionConfigView> {
  const r = await fetch(ROOT, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

export async function fetchCcPairs(): Promise<SharePointCcPairOption[]> {
  const r = await fetch(`${ROOT}/cc-pairs`);
  if (!r.ok) throw new Error(`GET ${ROOT}/cc-pairs -> ${r.status}`);
  return r.json();
}

export async function detectScopes(): Promise<ScopeDetectionResponse> {
  const r = await fetch(`${ROOT}/detect-scopes`, { method: "POST" });
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}
