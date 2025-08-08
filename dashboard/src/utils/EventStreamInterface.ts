import type { RobotData } from '../types/RobotData';

export interface EventStream {
  subscribe(callback: (robots: RobotData[]) => void): void;
  cleanup(): void;
}
