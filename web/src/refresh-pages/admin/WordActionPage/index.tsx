"use client";

import { useCallback, useEffect, useState } from "react";
import * as SettingsLayouts from "@/layouts/settings-layouts";
import { Section } from "@/layouts/general-layouts";
import {
  SvgCheckCircle,
  SvgFileText,
  SvgRefreshCw,
  SvgXOctagon,
} from "@opal/icons";
import { Button } from "@opal/components";
import { Card } from "@opal/layouts";
import Switch from "@/refresh-components/inputs/Switch";
import Text from "@/refresh-components/texts/Text";
import SimpleLoader from "@/refresh-components/loaders/SimpleLoader";
import { toast } from "@/hooks/useToast";
import { ADMIN_ROUTES } from "@/lib/admin-routes";
import {
  detectScopes,
  fetchCcPairs,
  fetchConfig,
  type SharePointActionConfigView,
  type SharePointCcPairOption,
  updateConfig,
} from "@/refresh-pages/admin/WordActionPage/svc";

const route = ADMIN_ROUTES.WORD_ACTION;


function StatusBadge({
  config,
}: {
  config: SharePointActionConfigView | null;
}) {
  if (!config || !config.is_enabled) {
    return (
      <Text mainUiAction text03>
        Disabled
      </Text>
    );
  }
  if (config.write_scopes_available) {
    return (
      <Section flexDirection="row" alignItems="center" gap={0.25}>
        <SvgCheckCircle size={16} className="text-status-success-05" />
        <Text mainUiAction text03>
          SharePoint write enabled
        </Text>
      </Section>
    );
  }
  return (
    <Section flexDirection="row" alignItems="center" gap={0.25}>
      <SvgXOctagon size={16} className="text-status-error-05" />
      <Text mainUiAction text03>
        Read-only (fallback to download)
      </Text>
    </Section>
  );
}


export default function WordActionPage() {
  const [config, setConfig] = useState<SharePointActionConfigView | null>(null);
  const [ccPairs, setCcPairs] = useState<SharePointCcPairOption[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [detecting, setDetecting] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const [cfg, pairs] = await Promise.all([fetchConfig(), fetchCcPairs()]);
      setConfig(cfg);
      setCcPairs(pairs);
    } catch (err) {
      toast.error(`Failed to load Word action config: ${(err as Error).message}`);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  async function handleToggleEnabled(next: boolean) {
    setSaving(true);
    try {
      const updated = await updateConfig({ is_enabled: next });
      setConfig(updated);
    } catch (err) {
      toast.error(`Save failed: ${(err as Error).message}`);
    } finally {
      setSaving(false);
    }
  }

  async function handleSelectCcPair(value: string) {
    const id = value === "" ? null : Number(value);
    setSaving(true);
    try {
      const updated = await updateConfig({ cc_pair_id: id });
      setConfig(updated);
    } catch (err) {
      toast.error(`Save failed: ${(err as Error).message}`);
    } finally {
      setSaving(false);
    }
  }

  async function handleToggleDownload(next: boolean) {
    setSaving(true);
    try {
      const updated = await updateConfig({
        allow_download_when_sp_available: next,
      });
      setConfig(updated);
    } catch (err) {
      toast.error(`Save failed: ${(err as Error).message}`);
    } finally {
      setSaving(false);
    }
  }

  async function handleDetect() {
    if (!config?.cc_pair_id) {
      toast.error("Select a SharePoint connector before detecting scopes.");
      return;
    }
    setDetecting(true);
    try {
      const result = await detectScopes();
      await refresh();
      if (result.has_write) {
        toast.success("SharePoint write enabled.");
      } else {
        toast.error(
          `Read-only — missing scopes: ${result.missing_scopes.join(", ") || "(none reported)"}.`
        );
      }
    } catch (err) {
      toast.error(`Detection failed: ${(err as Error).message}`);
    } finally {
      setDetecting(false);
    }
  }

  if (loading) {
    return (
      <SettingsLayouts.Root>
        <SettingsLayouts.Header
          icon={route.icon}
          title={route.title}
          description="Configure the Word generation action and its SharePoint integration."
          separator
        />
        <SettingsLayouts.Body>
          <SimpleLoader />
        </SettingsLayouts.Body>
      </SettingsLayouts.Root>
    );
  }

  return (
    <SettingsLayouts.Root>
      <SettingsLayouts.Header
        icon={route.icon}
        title={route.title}
        description="Generate Word (.docx) files in chat. When SharePoint is configured, users save the file directly to a folder where they have write access."
        separator
      />

      <SettingsLayouts.Body>
        <div className="border border-border-02 rounded-lg p-4 bg-background-neutral-00">
          <Card.Header
            sizePreset="main-ui"
            variant="section"
            icon={SvgFileText}
            title="Word generation"
            description="Master toggle for the entire action. Users won't see the action in chat if this is off."
            rightChildren={
              <Switch
                checked={config?.is_enabled ?? false}
                disabled={saving}
                onCheckedChange={handleToggleEnabled}
              />
            }
          />
        </div>

        <div className="border border-border-02 rounded-lg p-4 bg-background-neutral-00">
          <Card.Header
            sizePreset="main-ui"
            variant="section"
            icon={SvgFileText}
            title="SharePoint connector"
            description="Pick which SharePoint connector this action uses. The action reuses its app-registration credentials — no extra setup."
            rightChildren={
              <select
                className="border border-border-02 rounded-md px-2 py-1 bg-background-neutral-00 text-text-04"
                value={config?.cc_pair_id?.toString() ?? ""}
                disabled={saving}
                onChange={(e) => handleSelectCcPair(e.target.value)}
              >
                <option value="">(none)</option>
                {ccPairs.map((p) => (
                  <option key={p.cc_pair_id} value={p.cc_pair_id}>
                    {p.name}
                  </option>
                ))}
              </select>
            }
          />
        </div>

        <div className="border border-border-02 rounded-lg p-4 bg-background-neutral-00">
          <Card.Header
            sizePreset="main-ui"
            variant="section"
            icon={SvgRefreshCw}
            title="SharePoint write capability"
            description="Detect whether the Azure AD app has Sites.ReadWrite.All or Files.ReadWrite.All consented. If not, users will only see the Download option."
            rightChildren={
              <Section flexDirection="row" alignItems="center" gap={0.5}>
                <StatusBadge config={config} />
                <Button
                  prominence="tertiary"
                  size="sm"
                  icon={SvgRefreshCw}
                  onClick={handleDetect}
                  disabled={detecting || !config?.cc_pair_id}
                  tooltip="Re-detect scopes via Graph token"
                >
                  Detect
                </Button>
              </Section>
            }
            bottomRightChildren={
              config && config.detected_roles.length > 0 ? (
                <Text mainUiBody text03>
                  Detected roles: {config.detected_roles.join(", ")}
                </Text>
              ) : null
            }
          />
        </div>

        <div className="border border-border-02 rounded-lg p-4 bg-background-neutral-00">
          <Card.Header
            sizePreset="main-ui"
            variant="section"
            icon={SvgFileText}
            title="Allow download when SharePoint is available"
            description="OFF (recommended) forces users to save to SharePoint when write access exists, so the doc reingests into Onyx KB. ON shows both Download and Save side-by-side."
            rightChildren={
              <Switch
                checked={config?.allow_download_when_sp_available ?? false}
                disabled={saving}
                onCheckedChange={handleToggleDownload}
              />
            }
          />
        </div>
      </SettingsLayouts.Body>
    </SettingsLayouts.Root>
  );
}
