import type { Vector2D } from "./Vector2D";

export type RobotData = {
  id: number; // Unique identifier for the robot
  position: Vector2D; //X,Z position, the Y is always 0
  isLeader: boolean; //true if the robot is the leader
  neighbors: number[]; //array of ids of the neighbors
  orientation: number; //degrees of rotation on the XZ plane, 0 is facing positive X
};
