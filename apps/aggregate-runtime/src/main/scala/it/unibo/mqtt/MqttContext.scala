package it.unibo.mqtt

import org.eclipse.paho.client.mqttv3.MqttClient

class MqttContext(url: String) {
  val client = MqttClient(url, MqttClient.generateClientId())
}
