import type { CommandPublisher } from './CommandPublisherInterface';
import type { Vector2D } from '../types/Vector2D';

export class FakeCommandPublisher implements CommandPublisher {
  publishCommand(command: Vector2D): void {
    console.warn('Fake command published:', command);
  }

  publishMoveCommand(robotId: number, command: Vector2D): void {
    console.warn(`Fake move command for robot ${robotId}:`, command);
  }

  publishLeaderCommand(robotId: number, isLeader: boolean): void {
    console.warn(`Fake leader command for robot ${robotId}:`, isLeader);
  }
}
