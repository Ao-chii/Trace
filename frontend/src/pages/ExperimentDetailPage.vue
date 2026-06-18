<script setup lang="ts">
import { onMounted, ref, watch } from "vue";
import { ArrowLeft, Database, RefreshCw } from "@lucide/vue";
import { getExperimentMetrics } from "../api/experiments";
import CaptureMatrix from "../components/experiment/CaptureMatrix.vue";
import CleanRunTable from "../components/experiment/CleanRunTable.vue";
import ExperimentMetricsTable from "../components/experiment/ExperimentMetricsTable.vue";
import ReplayEvidencePanel from "../components/experiment/ReplayEvidencePanel.vue";
import { demoExperimentMetrics } from "../demo/staticRunFixture";
import { useLatestRequest } from "../composables/useLatestRequest";
import { useI18n } from "../i18n";
import type { ExperimentMetricsResponse } from "../types/api";
import type { DataSource } from "../types/ui";

const props = defineProps<{
  experimentId: string;
  dataSource: DataSource;
}>();

const emit = defineEmits<{
  navigate: [hash: string];
}>();

const { t } = useI18n();

const metrics = ref<ExperimentMetricsResponse | null>(null);
const loading = ref(false);
const errorMessage = ref<string | null>(null);
const selectedReplayId = ref<string | null>(null);

const metricsRequest = useLatestRequest();

function formatDate(value: string | null): string {
  if (!value) {
    return t("common.none");
  }
  return new Date(value).toLocaleString();
}

function selectReplay(replayId: string) {
  selectedReplayId.value = replayId;
}

function openCleanRun(runId: string) {
  emit("navigate", `#/runs/${runId}`);
}

async function loadMetrics() {
  const requestSeq = metricsRequest.next();
  loading.value = true;
  errorMessage.value = null;
  selectedReplayId.value = null;
  try {
    const next = props.dataSource === "demo" ? demoExperimentMetrics : await getExperimentMetrics(props.experimentId);
    if (!metricsRequest.isCurrent(requestSeq)) {
      return;
    }
    metrics.value = next;
  } catch (error) {
    if (!metricsRequest.isCurrent(requestSeq)) {
      return;
    }
    metrics.value = null;
    errorMessage.value = error instanceof Error ? error.message : t("experiments.metricsFailed");
  } finally {
    if (metricsRequest.isCurrent(requestSeq)) {
      loading.value = false;
    }
  }
}

onMounted(() => {
  void loadMetrics();
});

watch(
  () => [props.experimentId, props.dataSource],
  () => {
    void loadMetrics();
  }
);
</script>

<template>
  <main class="experiment-detail app-page">
    <section class="detail-head">
      <button class="text-button" type="button" @click="emit('navigate', '#/experiments')">
        <ArrowLeft :size="16" aria-hidden="true" />
        {{ t("experiments.back") }}
      </button>
      <button class="text-button" type="button" @click="loadMetrics">
        <RefreshCw :size="16" aria-hidden="true" />
        {{ t("projects.refresh") }}
      </button>
    </section>

    <p v-if="errorMessage" class="error-banner">{{ errorMessage }}</p>
    <p v-if="loading" class="mode-note">{{ t("experiments.loadingMetrics") }}</p>

    <template v-if="metrics">
      <section class="hero-band subtle-panel">
        <div>
          <p class="eyebrow">TRACE / EXPERIMENT DETAIL</p>
          <h1>{{ metrics.experiment.name }}</h1>
          <p>{{ t("experiments.detailSubtitle") }}</p>
        </div>
        <div class="source-card">
          <Database :size="18" aria-hidden="true" />
          <span>
            <strong>{{ metrics.data_source.kind === "real_llm" ? t("experiments.realLlm") : t("experiments.mockSource") }}</strong>
            <small>{{ metrics.data_source.label }} · {{ metrics.data_source.generated_by }}</small>
          </span>
        </div>
      </section>

      <section class="metadata-grid">
        <article class="subtle-panel meta-tile">
          <span>{{ t("experiments.provider") }}</span>
          <strong>{{ metrics.data_source.provider_label ?? t("common.unknown") }}</strong>
          <small>{{ metrics.data_source.model_label ?? t("common.unknown") }}</small>
        </article>
        <article class="subtle-panel meta-tile">
          <span>{{ t("experiments.repeat") }}</span>
          <strong>{{ metrics.experiment.repeat_count }}</strong>
          <button class="inline-link" type="button" @click="emit('navigate', `#/datasets/${metrics.experiment.dataset_id}`)">
            {{ metrics.experiment.dataset_id }}
          </button>
        </article>
        <article class="subtle-panel meta-tile">
          <span>{{ t("experiments.generatedAt") }}</span>
          <strong>{{ formatDate(metrics.generated_at) }}</strong>
          <small>{{ t(`status.${metrics.experiment.status}`) }}</small>
        </article>
        <article class="subtle-panel meta-tile">
          <span>{{ t("experiments.cleanRuns") }}</span>
          <strong>{{ metrics.clean_runs.length }}</strong>
          <small>{{ metrics.replay_runs.length }} {{ t("experiments.replays") }}</small>
        </article>
      </section>

      <ExperimentMetricsTable :rows="metrics.rows" />
      <CaptureMatrix
        :rows="metrics.rows"
        :capture-matrix="metrics.capture_matrix"
        :capture-matrix-counts="metrics.capture_matrix_counts"
      />
      <CleanRunTable :clean-runs="metrics.clean_runs" @open-run="openCleanRun" />
      <ReplayEvidencePanel
        :replay-runs="metrics.replay_runs"
        :replay-results="metrics.experiment_replay_runs"
        :selected-replay-id="selectedReplayId"
        @select="selectReplay"
      />
    </template>
  </main>
</template>

<style scoped>
.detail-head,
.hero-band,
.metadata-grid {
  margin-top: 18px;
}

.detail-head {
  display: flex;
  justify-content: space-between;
  gap: 12px;
}

.hero-band {
  display: flex;
  justify-content: space-between;
  gap: 20px;
  padding: 18px;
}

.hero-band h1 {
  margin: 8px 0 10px;
}

.hero-band p {
  max-width: 760px;
  font-family: var(--font-serif);
}

.source-card {
  width: min(360px, 100%);
  display: flex;
  gap: 10px;
  align-items: flex-start;
  padding: 12px;
  border: 1px solid var(--border);
  border-radius: 7px;
  background: rgba(251, 250, 247, 0.78);
}

.source-card span {
  display: grid;
  gap: 2px;
  min-width: 0;
}

.source-card small,
.meta-tile small {
  overflow-wrap: anywhere;
  color: var(--muted);
  font-family: var(--font-mono);
  font-size: 11px;
}

.metadata-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
}

.meta-tile {
  display: grid;
  gap: 3px;
  padding: 14px;
}

.meta-tile span {
  color: var(--muted);
  font-family: var(--font-mono);
  font-size: 11px;
  text-transform: uppercase;
}

.meta-tile strong {
  overflow-wrap: anywhere;
  font-size: 20px;
}

.meta-tile small {
  overflow-wrap: anywhere;
  color: var(--muted);
  font-family: var(--font-mono);
  font-size: 11px;
}

.inline-link {
  justify-self: start;
  padding: 0;
  border: 0;
  background: transparent;
  color: var(--tool);
  font-family: var(--font-mono);
  font-size: 11px;
  font-weight: 700;
  overflow-wrap: anywhere;
  text-align: left;
}

.inline-link:hover {
  background: transparent;
  color: var(--ink);
  text-decoration: underline;
}

@media (max-width: 1080px) {
  .hero-band,
  .metadata-grid {
    grid-template-columns: 1fr;
  }

  .hero-band {
    display: grid;
  }
}
</style>
