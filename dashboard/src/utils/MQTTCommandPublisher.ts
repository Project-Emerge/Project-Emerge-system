import type { CommandPublisher } from './CommandPublisherInterface';
import type { Vector2D } from '../types/Vector2D';
import mqtt from 'mqtt';

export class MQTTCommandPublisher implements CommandPublisher {
  private brokerUrl: string;
  private client: mqtt.MqttClient;

  constructor(brokerUrl: string) {
    this.brokerUrl = brokerUrl;
    this.client = mqtt.connect(brokerUrl);
  }

  publishCommand(command: Vector2D): void {
    // Implement MQTT publishing logic here
    console.log(`MQTT command published to ${this.brokerUrl}:`, command);
  }
  publishMoveCommand(robotId: number, command: Vector2D): void {
    console.log(`MQTT move command for robot ${robotId} to ${this.brokerUrl}:`, command);
    
    const forward = command.y;  // Forward/backward speed
    const turn = command.x;     // Turn rate (positive = turn right)
    
    // Calculate differential drive motor values
    // For turning: left motor - turn, right motor + turn
    let left = forward - turn;
    let right = forward + turn;
    
    // Clamp values to valid range [-1, 1]
    left = Math.max(-1, Math.min(1, left));
    right = Math.max(-1, Math.min(1, right));
    
    console.log(`Motor values for robot ${robotId}:`, { left, right });
    
    this.client.publish(`robots/${robotId}/move`, JSON.stringify({left, right}));
  }

  publishLeaderCommand(robotId: number, isLeader: boolean): void {
    console.log(`MQTT leader command for robot ${robotId} to ${this.brokerUrl}:`, isLeader);
    // Implement MQTT logic for leader command
  }
}
