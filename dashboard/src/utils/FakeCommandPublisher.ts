import type { CommandPublisher } from './CommandPublisherInterface';
import type { Vector2D } from '../types/Vector2D';

export class FakeCommandPublisher implements CommandPublisher {
  publishCommand(command: Vector2D): void {
    console.log('Fake command published:', command);
  }

  publishMoveCommand(robotId: number, command: Vector2D): void {
    console.log(`Fake move command for robot ${robotId}:`, command);
  }

  publishLeaderCommand(robotId: number, isLeader: boolean): void {
    console.log(`Fake leader command for robot ${robotId}:`, isLeader);
  }
}
