<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { ArrowRight, Beaker, Play, Plus, RefreshCw, ServerCrash } from "@lucide/vue";
import { createExperiment, listExperiments, runExperiment } from "../api/experiments";
import { listEvalDatasets } from "../api/evaluation";
import { listStrategies, listStrategyVersions } from "../api/strategies";
import { demoExperimentMetrics } from "../demo/staticRunFixture";
import { useLatestRequest } from "../composables/useLatestRequest";
import { useI18n } from "../i18n";
import type { EvalDatasetOut, ExperimentDefinition, StrategyVersionOut } from "../types/api";
import type { DataSource } from "../types/ui";

const props = defineProps<{
  dataSource: DataSource;
}>();

const emit = defineEmits<{
  navigate: [hash: string];
}>();

const { t } = useI18n();

const experiments = ref<ExperimentDefinition[]>([]);
const datasets = ref<EvalDatasetOut[]>([]);
const strategyVersions = ref<StrategyVersionOut[]>([]);
const loading = ref(false);
const creating = ref(false);
const startingId = ref<string | null>(null);
const errorMessage = ref<string | null>(null);
const createError = ref<string | null>(null);
const form = ref({
  id: "",
  name: "local demo experiment",
  datasetId: "",
  strategyVersionIds: [] as string[],
  repeatCount: 1,
  provider: "mock",
  model: "mock-1"
});

const listRequest = useLatestRequest();

const completedCount = computed(() => experiments.value.filter((experiment) => experiment.status === "completed").length);
const canCreate = computed(
  () =>
    props.dataSource === "api" &&
    form.value.name.trim() &&
    form.value.datasetId &&
    form.value.strategyVersionIds.length > 0 &&
    form.value.repeatCount >= 1
);

// llm_override is only the override layer; the resolved final provider/model is
// frozen per clean run's strategy_snapshot (see Experiment Detail). On the list we
// show the override when present, otherwise "strategy default" — not "unknown".
function providerLabel(experiment: ExperimentDefinition): string {
  return experiment.llm_override?.provider ?? t("experiments.strategyDefault");
}

function modelLabel(experiment: ExperimentDefinition): string {
  return experiment.llm_override?.model ?? t("experiments.strategyDefault");
}

function formatDate(value: string | null): string {
  if (!value) {
    return t("common.none");
  }
  return new Date(value).toLocaleString();
}

function openExperiment(experimentId: string) {
  emit("navigate", `#/experiments/${experimentId}`);
}

async function loadExperiments() {
  const requestSeq = listRequest.next();
  loading.value = true;
  errorMessage.value = null;
  try {
    const next = props.dataSource === "demo" ? [demoExperimentMetrics.experiment] : await listExperiments();
    if (!listRequest.isCurrent(requestSeq)) {
      return;
    }
    experiments.value = next;
  } catch (error) {
    if (!listRequest.isCurrent(requestSeq)) {
      return;
    }
    experiments.value = [];
    errorMessage.value = error instanceof Error ? error.message : t("experiments.loadFailed");
  } finally {
    if (listRequest.isCurrent(requestSeq)) {
      loading.value = false;
    }
  }
}

async function loadCreateOptions() {
  if (props.dataSource !== "api") {
    datasets.value = [];
    strategyVersions.value = [];
    return;
  }
  try {
    const [nextDatasets, strategies] = await Promise.all([listEvalDatasets(), listStrategies()]);
    const versionGroups = await Promise.all(strategies.map((strategy) => listStrategyVersions(strategy.id)));
    datasets.value = nextDatasets;
    strategyVersions.value = versionGroups.flat();
    if (!form.value.datasetId && nextDatasets[0]) {
      form.value.datasetId = nextDatasets[0].id;
    }
    if (form.value.strategyVersionIds.length === 0) {
      form.value.strategyVersionIds = versionGroups.flat().map((version) => version.id);
    }
  } catch (error) {
    createError.value = error instanceof Error ? error.message : t("experiments.optionsFailed");
  }
}

function toggleStrategy(versionId: string) {
  const current = new Set(form.value.strategyVersionIds);
  if (current.has(versionId)) {
    current.delete(versionId);
  } else {
    current.add(versionId);
  }
  form.value.strategyVersionIds = [...current];
}

async function submitExperiment() {
  if (!canCreate.value) {
    return;
  }
  creating.value = true;
  createError.value = null;
  try {
    const override =
      form.value.provider.trim() || form.value.model.trim()
        ? {
            provider: form.value.provider.trim() || null,
            model: form.value.model.trim() || null
          }
        : null;
    const created = await createExperiment({
      id: form.value.id.trim() || null,
      name: form.value.name.trim(),
      dataset_id: form.value.datasetId,
      strategy_version_ids: form.value.strategyVersionIds,
      repeat_count: Number(form.value.repeatCount) || 1,
      llm_override: override
    });
    experiments.value = [created, ...experiments.value.filter((item) => item.id !== created.id)];
    openExperiment(created.id);
  } catch (error) {
    createError.value = error instanceof Error ? error.message : t("experiments.createFailed");
  } finally {
    creating.value = false;
  }
}

async function startExperiment(experiment: ExperimentDefinition) {
  if (props.dataSource !== "api") {
    openExperiment(experiment.id);
    return;
  }
  startingId.value = experiment.id;
  errorMessage.value = null;
  try {
    const updated = await runExperiment(experiment.id);
    experiments.value = experiments.value.map((item) => (item.id === updated.id ? updated : item));
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : t("experiments.startFailed");
  } finally {
    startingId.value = null;
  }
}

onMounted(() => {
  void loadExperiments();
  void loadCreateOptions();
});

watch(
  () => props.dataSource,
  () => {
    void loadExperiments();
    void loadCreateOptions();
  }
);
</script>

<template>
  <main class="experiment-page app-page">
    <section class="experiment-head">
      <div>
        <p class="eyebrow">TRACE / EXPERIMENTS</p>
        <h1>{{ t("experiments.title") }}</h1>
        <p>{{ t("experiments.subtitle") }}</p>
      </div>
      <div class="head-actions">
        <button v-if="props.dataSource === 'api'" class="text-button" type="button" @click="emit('navigate', '#/datasets/new')">
          <Plus :size="16" aria-hidden="true" />
          {{ t("datasets.create") }}
        </button>
        <button class="text-button" type="button" @click="loadExperiments">
          <RefreshCw :size="16" aria-hidden="true" />
          {{ t("projects.refresh") }}
        </button>
      </div>
    </section>

    <p v-if="errorMessage" class="error-banner">{{ errorMessage }}</p>
    <p v-if="createError" class="error-banner">{{ createError }}</p>

    <section class="summary-strip">
      <article class="subtle-panel summary-tile">
        <span>{{ t("experiments.total") }}</span>
        <strong>{{ experiments.length }}</strong>
      </article>
      <article class="subtle-panel summary-tile">
        <span>{{ t("experiments.completed") }}</span>
        <strong>{{ completedCount }}</strong>
      </article>
      <article class="subtle-panel summary-tile">
        <span>{{ t("experiments.source") }}</span>
        <strong>{{ props.dataSource === "demo" ? t("app.demo") : t("app.api") }}</strong>
      </article>
    </section>

    <section v-if="props.dataSource === 'api'" class="subtle-panel create-panel">
      <div class="panel-title">
        <div>
          <p class="eyebrow">CREATE EXPERIMENT</p>
          <h2>{{ t("experiments.createTitle") }}</h2>
        </div>
        <button class="text-button" type="button" :disabled="!canCreate || creating" @click="submitExperiment">
          <Plus :size="16" aria-hidden="true" />
          {{ creating ? t("experiments.creating") : t("experiments.create") }}
        </button>
      </div>
      <div class="create-grid">
        <label>
          <span>{{ t("experiments.optionalId") }}</span>
          <input v-model="form.id" type="text" placeholder="exp-local-demo" />
        </label>
        <label>
          <span>{{ t("experiments.name") }}</span>
          <input v-model="form.name" type="text" />
        </label>
        <label>
          <span>{{ t("experiments.dataset") }}</span>
          <select v-model="form.datasetId">
            <option v-for="dataset in datasets" :key="dataset.id" :value="dataset.id">
              {{ dataset.name }} / {{ dataset.version }}
            </option>
          </select>
        </label>
        <label>
          <span>{{ t("experiments.repeat") }}</span>
          <input v-model.number="form.repeatCount" type="number" min="1" max="20" />
        </label>
        <label>
          <span>{{ t("experiments.provider") }}</span>
          <input v-model="form.provider" type="text" placeholder="mock" />
        </label>
        <label>
          <span>{{ t("experiments.model") }}</span>
          <input v-model="form.model" type="text" placeholder="mock-1" />
        </label>
      </div>
      <div class="strategy-picker">
        <span>{{ t("experiments.strategy") }}</span>
        <div class="strategy-options">
          <button
            v-for="version in strategyVersions"
            :key="version.id"
            type="button"
            :class="{ selected: form.strategyVersionIds.includes(version.id) }"
            @click="toggleStrategy(version.id)"
          >
            <strong>{{ version.name }}</strong>
            <small>{{ version.id }}</small>
          </button>
        </div>
      </div>
    </section>

    <section class="table-shell experiment-table">
      <table>
        <thead>
          <tr>
            <th>{{ t("experiments.name") }}</th>
            <th>{{ t("experiments.dataset") }}</th>
            <th>{{ t("experiments.status") }}</th>
            <th>{{ t("experiments.strategies") }}</th>
            <th>{{ t("experiments.repeat") }}</th>
            <th>{{ t("experiments.model") }}</th>
            <th>{{ t("experiments.time") }}</th>
            <th>{{ t("experiments.action") }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="experiment in experiments" :key="experiment.id">
            <td>
              <button class="link-button" type="button" @click="openExperiment(experiment.id)">
                <Beaker :size="16" aria-hidden="true" />
                <span>
                  <strong>{{ experiment.name }}</strong>
                  <small>{{ experiment.id }}</small>
                </span>
              </button>
            </td>
            <td class="mono">{{ experiment.dataset_id }}</td>
            <td>
              <span class="status-pill" :data-status="experiment.status">{{ t(`status.${experiment.status}`) }}</span>
            </td>
            <td>{{ experiment.strategy_version_ids.length }}</td>
            <td>{{ experiment.repeat_count }}</td>
            <td>
              <span class="model-cell">
                <strong>{{ providerLabel(experiment) }}</strong>
                <small>{{ modelLabel(experiment) }}</small>
              </span>
            </td>
            <td>
              <span class="time-cell">
                <small>{{ t("experiments.started") }}: {{ formatDate(experiment.started_at) }}</small>
                <small>{{ t("experiments.finished") }}: {{ formatDate(experiment.finished_at) }}</small>
              </span>
            </td>
            <td>
              <span class="row-actions">
                <button class="text-button" type="button" @click="openExperiment(experiment.id)">
                  <ArrowRight :size="15" aria-hidden="true" />
                  {{ t("experiments.open") }}
                </button>
                <button
                  v-if="props.dataSource === 'api' && ['draft', 'queued', 'cancelled'].includes(experiment.status)"
                  class="text-button"
                  type="button"
                  :disabled="startingId === experiment.id"
                  @click="startExperiment(experiment)"
                >
                  <Play :size="15" aria-hidden="true" />
                  {{ startingId === experiment.id ? t("experiments.starting") : t("run.start") }}
                </button>
              </span>
            </td>
          </tr>
        </tbody>
      </table>
      <div v-if="!loading && experiments.length === 0" class="empty-state">
        <ServerCrash :size="24" aria-hidden="true" />
        <h2>{{ t("experiments.emptyTitle") }}</h2>
        <p>{{ t("experiments.emptyBody") }}</p>
      </div>
      <p v-if="loading" class="mode-note">{{ t("experiments.loading") }}</p>
    </section>
  </main>
</template>

<style scoped>
.experiment-head {
  display: flex;
  gap: 16px;
  align-items: flex-start;
  justify-content: space-between;
  padding: 18px 0 24px;
  border-bottom: 1px solid var(--border);
}

.experiment-head h1 {
  margin: 8px 0 10px;
}

.experiment-head p {
  max-width: 760px;
  font-family: var(--font-serif);
}

.head-actions,
.row-actions {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

.summary-strip {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px;
  margin-top: 18px;
}

.summary-tile {
  display: grid;
  gap: 3px;
  padding: 14px;
}

.summary-tile span,
.time-cell small,
.model-cell small {
  color: var(--muted);
  font-family: var(--font-mono);
  font-size: 11px;
}

.summary-tile strong {
  font-size: 24px;
  line-height: 1.2;
}

.experiment-table {
  margin-top: 18px;
}

.create-panel {
  display: grid;
  gap: 14px;
  margin-top: 18px;
  padding: 16px;
}

.panel-title {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  align-items: flex-start;
}

.panel-title h2 {
  margin-top: 4px;
  font-size: 18px;
}

.create-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px;
}

.create-grid label,
.strategy-picker {
  display: grid;
  gap: 6px;
}

.create-grid span,
.strategy-picker > span {
  color: var(--muted);
  font-family: var(--font-mono);
  font-size: 11px;
  text-transform: uppercase;
}

.create-grid input,
.create-grid select {
  width: 100%;
  min-height: 34px;
  padding: 6px 9px;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: var(--panel);
  color: var(--ink);
}

.strategy-options {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 8px;
}

.strategy-options button {
  display: grid;
  gap: 2px;
  justify-items: start;
  min-height: 52px;
  padding: 9px;
  text-align: left;
}

.strategy-options button.selected {
  border-color: var(--tool);
  background: rgba(49, 95, 125, 0.08);
  box-shadow: inset 3px 0 0 var(--tool);
}

.strategy-options small {
  color: var(--muted);
  font-family: var(--font-mono);
  font-size: 11px;
  overflow-wrap: anywhere;
}

.link-button {
  display: inline-flex;
  align-items: flex-start;
  gap: 8px;
  padding: 0;
  border: 0;
  background: transparent;
  text-align: left;
}

.link-button:hover {
  background: transparent;
  color: var(--ink);
}

.link-button span,
.model-cell,
.time-cell {
  display: grid;
  gap: 2px;
  min-width: 0;
}

.link-button small,
.time-cell small {
  overflow-wrap: anywhere;
}

.status-pill {
  display: inline-flex;
  min-height: 24px;
  align-items: center;
  padding: 2px 8px;
  border: 1px solid var(--border);
  border-radius: 999px;
  background: var(--panel-soft);
  color: var(--muted-strong);
  font-size: 12px;
  font-weight: 700;
}

.status-pill[data-status="completed"] {
  border-color: rgba(47, 107, 63, 0.24);
  background: var(--passed-bg);
  color: var(--passed);
}

.status-pill[data-status="running"],
.status-pill[data-status="queued"] {
  border-color: rgba(138, 90, 0, 0.2);
  background: var(--running-bg);
  color: var(--running);
}

.status-pill[data-status="failed"],
.status-pill[data-status="cancelled"] {
  border-color: rgba(159, 58, 47, 0.24);
  background: var(--failed-bg);
  color: var(--failed);
}

.empty-state {
  display: grid;
  gap: 10px;
  justify-items: center;
  padding: 34px;
  text-align: center;
}

@media (max-width: 900px) {
  .experiment-head,
  .summary-strip,
  .create-grid,
  .strategy-options {
    grid-template-columns: 1fr;
  }

  .experiment-head {
    display: grid;
  }
}
</style>
