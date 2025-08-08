package view.fx

import scalafx.Includes.jfxNode2sfx
import scalafx.scene.layout.Pane

/**
 * Pimping functions to simplify some operations on a Pane.
 */
extension (pane: Pane)
  def isAlreadyIn(id: String): Boolean =
    pane.children.exists(_.id.value == id)
