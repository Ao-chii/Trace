import { requestJson } from "./client";
import type { StrategyOut, StrategyVersionOut } from "../types/api";

export function listStrategies(): Promise<StrategyOut[]> {
  return requestJson<StrategyOut[]>("/api/v1/strategies");
}

export function listStrategyVersions(strategyId: string): Promise<StrategyVersionOut[]> {
  return requestJson<StrategyVersionOut[]>(`/api/v1/strategies/${strategyId}/versions`);
}

export function getStrategyVersion(strategyVersionId: string): Promise<StrategyVersionOut> {
  return requestJson<StrategyVersionOut>(`/api/v1/strategy-versions/${strategyVersionId}`);
}
