// Client-side helpers for the Word/SharePoint user-facing flows.

export interface ArtifactCapabilityView {
  enabled: boolean;
  write_scopes_available: boolean;
  allow_download_when_sp_available: boolean;
}

export interface SiteView {
  id: string;
  display_name: string;
  web_url: string;
}

export interface DriveView {
  id: string;
  name: string;
  web_url: string | null;
}

export interface FolderView {
  id: string;
  name: string;
  web_url: string | null;
  can_write: boolean;
}

export interface UploadResultView {
  item_id: string;
  drive_id: string;
  web_url: string;
  filename: string;
}

export interface ArtifactStatusView {
  exists: boolean;
  saved_web_url: string | null;
}

const ROOT = "/api/sharepoint-action";

export async function fetchCapabilities(): Promise<ArtifactCapabilityView> {
  const r = await fetch(`${ROOT}/capabilities`);
  if (!r.ok) throw new Error(`GET capabilities -> ${r.status}`);
  return r.json();
}

export async function fetchArtifactStatus(
  fileId: string
): Promise<ArtifactStatusView> {
  const r = await fetch(`${ROOT}/artifact/${encodeURIComponent(fileId)}`);
  if (!r.ok) throw new Error(`GET artifact status -> ${r.status}`);
  return r.json();
}

export async function fetchSites(): Promise<SiteView[]> {
  const r = await fetch(`${ROOT}/sites`);
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

export async function fetchDrives(siteId: string): Promise<DriveView[]> {
  const r = await fetch(`${ROOT}/sites/${encodeURIComponent(siteId)}/drives`);
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

export async function fetchFolders(
  driveId: string,
  parentId = "root"
): Promise<FolderView[]> {
  const r = await fetch(
    `${ROOT}/drives/${encodeURIComponent(driveId)}/folders?parent_id=${encodeURIComponent(parentId)}`
  );
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

export async function uploadToSharePoint(payload: {
  file_id: string;
  drive_id: string;
  folder_id: string;
}): Promise<UploadResultView> {
  const r = await fetch(`${ROOT}/upload`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

export function downloadUrlForFileId(fileId: string): string {
  return `/api/chat/file/${encodeURIComponent(fileId)}`;
}
