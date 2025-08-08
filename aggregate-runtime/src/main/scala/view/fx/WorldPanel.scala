package view.fx

import scalafx.Includes._
import scalafx.application.Platform
import scalafx.scene.layout.Pane
import scalafx.scene.paint.Color
import scalafx.scene.shape.{Rectangle, Line}

/**
 * A panel that displays nodes and their orientations.
 *
 * @param pane  the pane to draw on
 * @param style the style of the nodes
 */
class WorldPanel(pane: Pane, style: NodeStyle):

  /**
   * Clears all children from the pane.
   */
  def clean(): Unit =
    Platform.runLater:
      pane.children.clear()

  /**
   * Draws a node at the specified position and direction.
   *
   * @param id        the identifier of the node
   * @param x         the x-coordinate of the node
   * @param y         the y-coordinate of the node
   * @param direction the direction of the node
   */
  def drawNodeAt(id: String, x: Double, y: Double, direction: Double): Unit = {
    Platform.runLater:
      if(id == "10") then
        println((id, direction))
      if !isAlreadyIn(id) then addNode(id, x, y, direction)
      else updateNode(id, x, y, direction)
  }

  /**
   * Adds a new node to the pane.
   *
   * @param id        the identifier of the node
   * @param x         the x-coordinate of the node
   * @param y         the y-coordinate of the node
   * @param direction the direction of the node
   */
  private def addNode(id: String, x: Double, y: Double, direction: Double): Unit =
    val node = Rectangle(x, y, style.width, style.height)
    node.id = id
    node.fill = style.color
    val orientationLine = createOrientationLine(id, x, y, direction)
    pane.children.addAll(node, orientationLine)

  /**
   * Updates the position and direction of an existing node.
   *
   * @param id        the identifier of the node
   * @param x         the new x-coordinate of the node
   * @param y         the new y-coordinate of the node
   * @param direction the new direction of the node
   */
  private def updateNode(id: String, x: Double, y: Double, direction: Double): Unit =
    val node: Rectangle = findNodeById[javafx.scene.shape.Rectangle](id)
    node.x = x
    node.y = y
    val orientationLine: Line = findNodeById[javafx.scene.shape.Line](s"${id}_orientation")
    updateOrientationLine(orientationLine, x, y, direction)

  /**
   * Creates a line representing the orientation of a node.
   *
   * @param id        the identifier of the node
   * @param x         the x-coordinate of the node
   * @param y         the y-coordinate of the node
   * @param direction the direction of the node
   * @return          the created orientation line
   */
  private def createOrientationLine(id: String, x: Double, y: Double, direction: Double): Line =
    val (centerX, centerY) = (x + style.width / 2, y + style.height / 2)
    val (deltaX, deltaY) = calculateDelta(centerX, centerY, direction)
    val line = Line(centerX, centerY, deltaX, deltaY)
    line.stroke = Color.Black
    line.strokeWidth = 3
    line.id = s"${id}_orientation"
    line

  /**
   * Updates the orientation line of a node.
   *
   * @param line      the orientation line to update
   * @param x         the x-coordinate of the node
   * @param y         the y-coordinate of the node
   * @param direction the direction of the node
   */
  private def updateOrientationLine(line: Line, x: Double, y: Double, direction: Double): Unit =
    val (centerX, centerY) = (x + style.width / 2, y + style.height / 2)
    val (deltaX, deltaY) = calculateDelta(centerX, centerY, direction)
    line.startX = centerX
    line.startY = centerY
    line.endX = deltaX
    line.endY = deltaY

  /**
   * Calculates the delta coordinates for the orientation line.
   *
   * @param centerX   the x-coordinate of the center of the node
   * @param centerY   the y-coordinate of the center of the node
   * @param direction the direction of the node
   * @return          a tuple containing the delta x and y coordinates
   */
  private def calculateDelta(centerX: Double, centerY: Double, direction: Double): (Double, Double) =
    val deltaX = centerX + (Math.cos(-direction) * 10)
    val deltaY = centerY - (Math.sin(-direction) * 10)
    (deltaX, deltaY)

  /**
   * Finds a node by its identifier.
   *
   * @param id the identifier of the node
   * @tparam T the type of the node
   * @return   the node with the specified identifier
   */
  private def findNodeById[T](id: String): T =
    pane.children.find(_.id.value == id).get.asInstanceOf[T]

  /**
   * Checks if a node with the specified identifier already exists in the pane.
   *
   * @param id the identifier of the node
   * @return   true if the node exists, false otherwise
   */
  private def isAlreadyIn(id: String): Boolean =
    pane.children.exists(_.id.value == id)

/**
 * A case class representing the style of a node.
 *
 * @param width  the width of the node
 * @param height the height of the node
 * @param color  the color of the node
 */
case class NodeStyle(width: Double, height: Double, color: Color)
