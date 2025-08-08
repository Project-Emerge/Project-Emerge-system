package view.fx

import it.unibo.core.{Boundary, Environment}
import it.unibo.demo.{ID, Info}

import scala.concurrent.{ExecutionContext, Future}

/**
 * A class responsible for rendering the environment and its nodes.
 * It is based on ScalaFX and uses two panels to render the world and the neighborhood.
 * @param worldPanel the panel to render the world
 * @param neighPanel the panel to render the neighborhood
 * @param magnifier the policy to magnify positions, default is identity
 * @param executionContext the execution context for futures
 */
class SimpleRender(
    worldPanel: WorldPanel,
    neighPanel: NeighborhoodPanel,
    magnifier: MagnifierPolicy = MagnifierPolicy.identity
)(using ExecutionContext)
    extends Boundary[Int, (Double, Double), Double]:
  
  override def output(environment: Environment[ID, (Double, Double), Info]): Future[Unit] =
    Future:
      worldPanel.clean()
      environment.nodes
        .map(id => id -> (environment.position(id) -> environment.sensing(id)))
        .map { case (id, (position, info)) => id -> (magnifier.magnify(position), info) }
        .foreach { case (id, (position, info)) => worldPanel.drawNodeAt(id.toString, position._1, position._2, info) }
      neighPanel.clean()
      environment.nodes
        .flatMap(id => environment.neighbors(id).map(id -> _))
        .foreach(link => neighPanel.drawNeighbours(link._1.toString, link._2.toString))

/**
 * A trait representing a policy to magnify positions.
 */
trait MagnifierPolicy:
  /**
   * Magnifies the given position.
   *
   * @param position the position to magnify
   * @return the magnified position
   */
  def magnify(position: (Double, Double)): (Double, Double)

/**
 * Companion object for MagnifierPolicy containing predefined policies.
 */
object MagnifierPolicy:
  /**
   * Creates a magnifier policy that scales positions by a given factor.
   *
   * @param scale the scale factor
   * @return a MagnifierPolicy that scales positions
   */
  def scale(scale: Double): MagnifierPolicy =
    (position: (Double, Double)) => (position._1 * scale, position._2 * scale)

  /**
   * Creates a magnifier policy that translates and scales positions.
   *
   * @param translation the translation to apply
   * @param scale the scale factor
   * @return a MagnifierPolicy that translates and scales positions
   */
  def translateAndScale(translation: (Double, Double), scale: Double): MagnifierPolicy =
    (position: (Double, Double)) => (position._1 * scale + translation._1, position._2 * scale + translation._2)

  /**
   * A magnifier policy that returns the position unchanged.
   *
   * @return a MagnifierPolicy that returns the position unchanged
   */
  def identity: MagnifierPolicy =
    (position: (Double, Double)) => position
