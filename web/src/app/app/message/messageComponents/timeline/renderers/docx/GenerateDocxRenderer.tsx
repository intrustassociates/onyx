"use client";

import { useEffect, useMemo, useState } from "react";
import {
  GenerateDocxPacket,
  GenerateDocxResult,
  GenerateDocxStart,
  PacketType,
} from "@/app/app/services/streamingModels";
import { MessageRenderer } from "@/app/app/message/messageComponents/interfaces";
import { SvgDownload, SvgFileText, SvgUploadCloud } from "@opal/icons";
import { Button } from "@opal/components";
import Text from "@/refresh-components/texts/Text";
import {
  downloadUrlForFileId,
  fetchArtifactStatus,
  fetchCapabilities,
  type ArtifactCapabilityView,
} from "@/app/app/message/messageComponents/timeline/renderers/docx/svc";
import SharePointSaveModal from "@/app/app/message/messageComponents/timeline/renderers/docx/SharePointSaveModal";


export const GenerateDocxRenderer: MessageRenderer<GenerateDocxPacket, {}> = ({
  packets,
  onComplete,
  children,
}) => {
  const start = useMemo(
    () =>
      packets.find((p) => p.obj.type === PacketType.GENERATE_DOCX_START)?.obj as
        | GenerateDocxStart
        | undefined,
    [packets]
  );
  const result = useMemo(
    () =>
      packets.find((p) => p.obj.type === PacketType.GENERATE_DOCX_RESULT)?.obj as
        | GenerateDocxResult
        | undefined,
    [packets]
  );
  const sectionEndOrError = useMemo(
    () =>
      packets.find(
        (p) =>
          p.obj.type === PacketType.SECTION_END ||
          p.obj.type === PacketType.ERROR
      ),
    [packets]
  );

  const [capability, setCapability] = useState<ArtifactCapabilityView | null>(
    null
  );
  const [savedUrl, setSavedUrl] = useState<string | null>(null);
  // The file_store entry was deleted/expired and was never saved — nothing
  // actionable left to offer.
  const [artifactMissing, setArtifactMissing] = useState(false);
  const [uploadFailed, setUploadFailed] = useState(false);
  const [modalOpen, setModalOpen] = useState(false);

  useEffect(() => {
    if (!result) return;
    let alive = true;
    fetchCapabilities()
      .then((c) => {
        if (alive) setCapability(c);
      })
      .catch(() => {
        if (alive)
          setCapability({
            enabled: false,
            write_scopes_available: false,
            allow_download_when_sp_available: true,
          });
      });
    // Restore post-upload state after a page reload: the packets only say
    // "generated", but the backend knows whether it was saved already.
    fetchArtifactStatus(result.file_id)
      .then((status) => {
        if (!alive) return;
        if (status.saved_web_url) setSavedUrl(status.saved_web_url);
        else if (!status.exists) setArtifactMissing(true);
      })
      .catch(() => {
        // Leave default state; buttons will surface errors on click.
      });
    return () => {
      alive = false;
    };
  }, [result]);

  const isComplete = !!result && !!sectionEndOrError;
  useEffect(() => {
    if (isComplete) onComplete();
  }, [isComplete, onComplete]);

  const sharePointAvailable =
    !!capability &&
    capability.enabled &&
    capability.write_scopes_available &&
    !uploadFailed;
  const showDownload =
    !!capability &&
    (!capability.enabled ||
      !capability.write_scopes_available ||
      capability.allow_download_when_sp_available ||
      uploadFailed);

  const status = !result
    ? `Generating Word document${start?.title ? `: ${start.title}` : "..."}`
    : savedUrl
      ? "Saved to SharePoint"
      : `Generated ${result.filename}`;

  const content = (
    <div className="flex flex-col gap-2">
      {!result && (
        <div className="flex items-center gap-2 text-sm text-text-03">
          <div className="flex gap-0.5">
            <div className="w-1 h-1 bg-current rounded-full animate-pulse" />
            <div
              className="w-1 h-1 bg-current rounded-full animate-pulse"
              style={{ animationDelay: "0.1s" }}
            />
            <div
              className="w-1 h-1 bg-current rounded-full animate-pulse"
              style={{ animationDelay: "0.2s" }}
            />
          </div>
          <span>Rendering with Pandoc...</span>
        </div>
      )}

      {result && (
        <div className="border border-border-02 rounded-md p-3 bg-background-neutral-01 flex items-center gap-3">
          <SvgFileText size={24} className="text-text-04" />
          <div className="flex flex-col flex-1 min-w-0">
            <Text mainUiAction text05>
              {result.filename}
            </Text>
            {savedUrl ? (
              <Text mainUiBody text03>
                Saved — Onyx will reindex after the next SharePoint sync.
              </Text>
            ) : artifactMissing ? (
              <Text mainUiBody text03>
                This document is no longer available.
              </Text>
            ) : uploadFailed ? (
              <Text mainUiBody text03>
                Upload failed. Retry or download the file instead.
              </Text>
            ) : (
              <Text mainUiBody text03>
                Generated. Choose where to save it.
              </Text>
            )}
          </div>

          {savedUrl ? (
            <a
              href={savedUrl}
              target="_blank"
              rel="noreferrer"
              className="text-text-link underline"
            >
              Open in SharePoint
            </a>
          ) : artifactMissing ? null : (
            <>
              {sharePointAvailable && (
                <Button
                  prominence="primary"
                  icon={SvgUploadCloud}
                  onClick={() => setModalOpen(true)}
                >
                  Save to SharePoint
                </Button>
              )}
              {uploadFailed && (
                <Button
                  prominence="secondary"
                  icon={SvgUploadCloud}
                  onClick={() => setModalOpen(true)}
                >
                  Retry save
                </Button>
              )}
              {showDownload && (
                <Button
                  prominence={sharePointAvailable ? "tertiary" : "primary"}
                  icon={SvgDownload}
                  href={downloadUrlForFileId(result.file_id)}
                >
                  Download
                </Button>
              )}
            </>
          )}
        </div>
      )}

      {modalOpen && result && (
        <SharePointSaveModal
          fileId={result.file_id}
          filename={result.filename}
          onClose={() => setModalOpen(false)}
          onSaved={(url) => {
            setSavedUrl(url);
            setUploadFailed(false);
            setModalOpen(false);
          }}
          onUploadError={() => setUploadFailed(true)}
        />
      )}
    </div>
  );

  return children([
    {
      icon: SvgFileText,
      status,
      content,
      supportsCollapsible: true,
    },
  ]);
};
