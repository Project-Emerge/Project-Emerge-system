import type { Vector2D } from '../types/Vector2D';
import type { RobotData } from '../types/RobotData';

export class FakeEventStream {
  private intervalId: NodeJS.Timeout | null = null;
  private robots: RobotData[];

  constructor(robotCount: number) {
    this.robots = this.generateFakeRobots(robotCount);
  }

  private generateFakeRobots(count: number): RobotData[] {
    const positions = Array.from({ length: count }, () => ({
      x: Math.random() * 20 - 10, // X position in range -10 to 10
      y: Math.random() * 20 - 10, // Z position in range -10 to 10
    }));
    return positions.map((pos, i) => ({
      id: i, // Add unique identifier
      position: pos,
      orientation: Math.random() * 360, // Initialize orientation randomly
      isLeader: i === 0,
      neighbors: [],
    }));
  }

  private updateRobotPositions(robots: RobotData[]): RobotData[] {
    const newPositions = robots.map((robot) => ({
      x: robot.position.x + (Math.random() - 0.5),
      y: robot.position.y + (Math.random() - 0.5),
    }));
    return robots.map((robot, i) => ({
      ...robot,
      position: newPositions[i],
      orientation: robot.orientation + (Math.random() - 0.5)*50,
      neighbors: this.calculateNearestNeighbors(newPositions[i], newPositions, i),
    }));
  }

  private calculateNearestNeighbors(
    currentPos: Vector2D,
    positions: Vector2D[],
    excludeIndex: number,
    distanceThreshold: number = 10 // Default threshold
  ): number[] {
    return positions
      .map((pos, j) => ({
        id: j,
        dist: Math.hypot(pos.x - currentPos.x, pos.y - currentPos.y),
      }))
      .filter((p) => p.id !== excludeIndex && p.dist <= distanceThreshold) // Filter by distance threshold
      .sort((a, b) => a.dist - b.dist)
      .slice(0, 3)
      .map((p) => p.id);
  }

  subscribe(callback: (robots: RobotData[]) => void): void {
    this.intervalId = setInterval(() => {
      this.robots = this.updateRobotPositions(this.robots);
      callback([...this.robots]);
    }, 200);
  }

  publishCommand(command: Vector2D): void {
    const leader = this.robots.find((robot) => robot.isLeader);
    if (leader) {
      console.log(`Fake command sent to leader robot:`, command);
    }
  }

  cleanup(): void {
    if (this.intervalId) {
      clearInterval(this.intervalId);
    }
  }
}
