import type { CommandPublisher } from '../types/CommandPublisher';
import type { MoveCommand } from '../types/MoveCommand';
import mqtt from 'mqtt';

export class MQTTCommandPublisher implements CommandPublisher {
  private client: mqtt.MqttClient;

  constructor(client: mqtt.MqttClient) {
    this.client = client;
  }
  publishProgramCommand(program: object): void {
    // Retained: a runtime that connects later (e.g. an edge runtime) immediately gets the current program.
    this.client.publish(`sensing`, JSON.stringify(program), { retain: true });
  }

  publishMoveCommand(robotId: number, command: MoveCommand): void {
    this.client.publish(`robots/${robotId}/move`, JSON.stringify({left: command.left, right: command.right}));
  }

  publishLeaderCommand(robotId: number): void {
   // Retained: the selected leader survives for runtimes that connect after it was chosen.
   this.client.publish(`leader`, JSON.stringify(robotId), { retain: true });
  }
}
