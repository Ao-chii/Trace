<script setup lang="ts">
import { computed } from "vue";
import { CheckCircle2, XCircle } from "@lucide/vue";
import { useI18n } from "../../i18n";
import type { ExperimentMetricRow } from "../../types/api";

const props = defineProps<{
  rows: ExperimentMetricRow[];
  captureMatrix: Record<string, Record<string, boolean>>;
  captureMatrixCounts?: Record<
    string,
    Record<string, { captured: boolean; captured_count: number; repeat_count: number; capture_rate: number }>
  >;
}>();

const { t } = useI18n();
const bugIds = computed(() => Object.keys(props.captureMatrix));
const strategies = computed(() => props.rows.map((row) => ({ id: row.strategy_id, name: row.strategy_name })));

function countLabel(bugId: string, strategyId: string): string {
  const stats = props.captureMatrixCounts?.[bugId]?.[strategyId];
  if (!stats) {
    return "";
  }
  return `${stats.captured_count}/${stats.repeat_count}`;
}
</script>

<template>
  <section class="section-stack">
    <div>
      <p class="eyebrow">CAPTURE MATRIX</p>
      <h2>{{ t("comparison.matrix") }}</h2>
    </div>
    <div class="table-shell">
      <table>
        <thead>
          <tr>
            <th>{{ t("comparison.bugVariant") }}</th>
            <th v-for="strategy in strategies" :key="strategy.id">{{ strategy.name }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="bugId in bugIds" :key="bugId">
            <td class="mono">{{ bugId }}</td>
            <td v-for="strategy in strategies" :key="strategy.id">
              <span v-if="captureMatrix[bugId]?.[strategy.id]" class="evidence-ok">
                <CheckCircle2 :size="16" aria-hidden="true" />
                {{ t("comparison.captured") }}
                <small v-if="countLabel(bugId, strategy.id)">{{ countLabel(bugId, strategy.id) }}</small>
              </span>
              <span v-else class="evidence-miss">
                <XCircle :size="16" aria-hidden="true" />
                {{ t("comparison.missed") }}
                <small v-if="countLabel(bugId, strategy.id)">{{ countLabel(bugId, strategy.id) }}</small>
              </span>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>
</template>

<style scoped>
.section-stack {
  display: grid;
  gap: 14px;
  margin-top: 18px;
}

.evidence-ok,
.evidence-miss {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-weight: 700;
}

.evidence-ok small,
.evidence-miss small {
  color: var(--muted);
  font-family: var(--font-mono);
  font-size: 11px;
}

.evidence-ok {
  color: var(--passed);
}

.evidence-miss {
  color: var(--failed);
}
</style>
