"use client";

import { useEffect, useState } from "react";
import {
  fetchDrives,
  fetchFolders,
  fetchSites,
  uploadToSharePoint,
  type DriveView,
  type FolderView,
  type SiteView,
} from "@/app/app/message/messageComponents/timeline/renderers/docx/svc";
import { Button } from "@opal/components";
import Text from "@/refresh-components/texts/Text";
import SimpleLoader from "@/refresh-components/loaders/SimpleLoader";
import { toast } from "@/hooks/useToast";
import { SvgFolder, SvgLock } from "@opal/icons";


export interface SharePointSaveModalProps {
  fileId: string;
  filename: string;
  onClose: () => void;
  onSaved: (webUrl: string) => void;
  /** Called when an upload attempt fails, so the parent card can offer a
   * Download escape hatch. The modal stays open for retries. */
  onUploadError?: (message: string) => void;
}


export default function SharePointSaveModal({
  fileId,
  filename,
  onClose,
  onSaved,
  onUploadError,
}: SharePointSaveModalProps) {
  const [sites, setSites] = useState<SiteView[]>([]);
  const [selectedSiteId, setSelectedSiteId] = useState<string | null>(null);
  const [drives, setDrives] = useState<DriveView[]>([]);
  const [selectedDriveId, setSelectedDriveId] = useState<string | null>(null);
  const [folders, setFolders] = useState<FolderView[]>([]);
  const [selectedFolderId, setSelectedFolderId] = useState<string | null>(null);

  const [loadingSites, setLoadingSites] = useState(true);
  const [loadingDrives, setLoadingDrives] = useState(false);
  const [loadingFolders, setLoadingFolders] = useState(false);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    let alive = true;
    fetchSites()
      .then((list) => {
        if (alive) setSites(list);
      })
      .catch((err) => toast.error(`Failed to load sites: ${err.message}`))
      .finally(() => {
        if (alive) setLoadingSites(false);
      });
    return () => {
      alive = false;
    };
  }, []);

  useEffect(() => {
    if (!selectedSiteId) {
      setDrives([]);
      return;
    }
    setLoadingDrives(true);
    fetchDrives(selectedSiteId)
      .then(setDrives)
      .catch((err) => toast.error(`Failed to load drives: ${err.message}`))
      .finally(() => setLoadingDrives(false));
    setSelectedDriveId(null);
    setSelectedFolderId(null);
    setFolders([]);
  }, [selectedSiteId]);

  useEffect(() => {
    if (!selectedDriveId) {
      setFolders([]);
      return;
    }
    setLoadingFolders(true);
    fetchFolders(selectedDriveId)
      .then(setFolders)
      .catch((err) => toast.error(`Failed to load folders: ${err.message}`))
      .finally(() => setLoadingFolders(false));
    setSelectedFolderId(null);
  }, [selectedDriveId]);

  async function handleSave() {
    if (!selectedDriveId || !selectedFolderId) {
      toast.error("Pick a folder first.");
      return;
    }
    setSaving(true);
    try {
      const result = await uploadToSharePoint({
        file_id: fileId,
        drive_id: selectedDriveId,
        folder_id: selectedFolderId,
      });
      toast.success(`Saved as ${result.filename}`);
      onSaved(result.web_url);
    } catch (err) {
      const message = (err as Error).message;
      toast.error(`Upload failed: ${message}`);
      onUploadError?.(message);
    } finally {
      setSaving(false);
    }
  }

  useEffect(() => {
    function handleKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [onClose]);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-background-overlay/60"
      onClick={onClose}
    >
      <div
        className="bg-background-neutral-00 border border-border-02 rounded-lg shadow-lg w-[640px] max-w-[95vw] max-h-[80vh] flex flex-col p-6 gap-4"
        onClick={(e) => e.stopPropagation()}
      >
        <Text mainContentEmphasis text05>
          Save to SharePoint
        </Text>
        <Text mainUiBody text03>
          Saving <b>{filename}</b>. You can only write into folders where you
          have edit permission in SharePoint.
        </Text>

        <div className="grid grid-cols-2 gap-3">
          <div>
            <Text mainUiAction text04>
              Site
            </Text>
            <select
              className="w-full border border-border-02 rounded-md px-2 py-1 mt-1 bg-background-neutral-00 text-text-04"
              value={selectedSiteId ?? ""}
              onChange={(e) => setSelectedSiteId(e.target.value || null)}
              disabled={loadingSites}
            >
              <option value="">{loadingSites ? "Loading..." : "Pick a site"}</option>
              {sites.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.display_name}
                </option>
              ))}
            </select>
          </div>

          <div>
            <Text mainUiAction text04>
              Library
            </Text>
            <select
              className="w-full border border-border-02 rounded-md px-2 py-1 mt-1 bg-background-neutral-00 text-text-04"
              value={selectedDriveId ?? ""}
              onChange={(e) => setSelectedDriveId(e.target.value || null)}
              disabled={!selectedSiteId || loadingDrives}
            >
              <option value="">
                {!selectedSiteId
                  ? "Pick a site first"
                  : loadingDrives
                    ? "Loading..."
                    : "Pick a library"}
              </option>
              {drives.map((d) => (
                <option key={d.id} value={d.id}>
                  {d.name}
                </option>
              ))}
            </select>
          </div>
        </div>

        <div className="border border-border-02 rounded-md overflow-y-auto flex-1 min-h-[200px] p-1">
          {!selectedDriveId ? (
            <div className="p-3 text-center text-text-03">
              <Text mainUiBody text03>
                Pick a site and library to browse folders.
              </Text>
            </div>
          ) : loadingFolders ? (
            <div className="p-3 flex justify-center">
              <SimpleLoader />
            </div>
          ) : folders.length === 0 ? (
            <div className="p-3 text-center">
              <Text mainUiBody text03>
                No folders found at the root of this library.
              </Text>
            </div>
          ) : (
            <ul>
              {folders.map((folder) => {
                const selected = folder.id === selectedFolderId;
                return (
                  <li key={folder.id}>
                    <button
                      type="button"
                      disabled={!folder.can_write}
                      onClick={() =>
                        folder.can_write && setSelectedFolderId(folder.id)
                      }
                      className={
                        "w-full flex items-center gap-2 px-2 py-1.5 rounded-md text-left " +
                        (selected
                          ? "bg-background-action-selected text-text-05"
                          : folder.can_write
                            ? "hover:bg-background-neutral-02 text-text-04"
                            : "text-text-02 cursor-not-allowed")
                      }
                    >
                      {folder.can_write ? (
                        <SvgFolder size={16} />
                      ) : (
                        <SvgLock size={16} />
                      )}
                      <span className="truncate">{folder.name}</span>
                      {!folder.can_write && (
                        <span className="ml-auto text-xs">read-only</span>
                      )}
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </div>

        <div className="flex justify-end gap-2">
          <Button prominence="tertiary" onClick={onClose} disabled={saving}>
            Cancel
          </Button>
          <Button
            prominence="primary"
            onClick={handleSave}
            disabled={!selectedFolderId || saving}
          >
            {saving ? "Saving..." : "Save"}
          </Button>
        </div>
      </div>
    </div>
  );
}
