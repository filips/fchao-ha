#pragma once

#include "esphome/core/component.h"
#include "esphome/components/uart/uart.h"
#include "esphome/components/sensor/sensor.h"
#include "esphome/components/binary_sensor/binary_sensor.h"
#include "esphome/components/switch/switch.h"

namespace esphome {
namespace fchao_inverter {

static const uint8_t START_MARKER = 0xAE;
static const uint8_t END_MARKER = 0xEE;
static const size_t MAX_FRAME_SIZE = 100;
static const size_t EXPECTED_PAYLOAD_SIZE = 14;
static const uint8_t MISSED_RESPONSE_THRESHOLD = 3;

// Commands (payload bytes, excluding frame markers and checksum)
static const uint8_t CMD_QUERY[] = {0x01, 0x01, 0x03};
static const uint8_t CMD_ON[] = {0x01, 0x02, 0x04, 0x00, 0x00};
static const uint8_t CMD_OFF[] = {0x01, 0x02, 0x04, 0x01, 0x00};

static uint8_t from_bcd(uint8_t bcd) { return ((bcd >> 4) * 10) + (bcd & 0x0F); }

static uint8_t to_bcd(uint8_t value) {
  value = value % 100;
  return ((value / 10) << 4) | (value % 10);
}

class FchaoInverterSwitch;

class FchaoInverter : public PollingComponent, public uart::UARTDevice {
 public:
  void set_voltage_out_sensor(sensor::Sensor *s) { voltage_out_ = s; }
  void set_voltage_in_sensor(sensor::Sensor *s) { voltage_in_ = s; }
  void set_power_sensor(sensor::Sensor *s) { power_ = s; }
  void set_temperature_sensor(sensor::Sensor *s) { temperature_ = s; }
  void set_fan_binary_sensor(binary_sensor::BinarySensor *s) { fan_ = s; }
  void set_status_binary_sensor(binary_sensor::BinarySensor *s) { status_ = s; }
  void set_output_switch(switch_::Switch *s) { output_switch_ = s; }
  void set_relay_switch(switch_::Switch *s) { relay_switch_ = s; }

  bool is_relay_on() {
    if (!relay_switch_) return true;
    return relay_switch_->state;
  }

  void setup() override {
    relay_was_on_ = is_relay_on();
    if (status_) status_->publish_state(false);
  }

  void update() override {
    bool relay_on = is_relay_on();

    if (!relay_on && relay_was_on_) {
      publish_unavailable_();
      relay_was_on_ = false;
    } else if (relay_on) {
      relay_was_on_ = true;
    }

    if (awaiting_response_) {
      missed_responses_++;
      if (missed_responses_ >= MISSED_RESPONSE_THRESHOLD) {
        if (status_) status_->publish_state(false);
        if (!relay_on) publish_unavailable_();
      }
    }

    awaiting_response_ = true;
    send_command_(CMD_QUERY, sizeof(CMD_QUERY));
  }

  void loop() override {

    while (available()) {
      uint8_t data;
      read_byte(&data);

      if (!in_frame_) {
        if (data == START_MARKER) {
          in_frame_ = true;
          rx_pos_ = 0;
          rx_buf_[rx_pos_++] = data;
        }
        continue;
      }

      if (rx_pos_ >= MAX_FRAME_SIZE) {
        in_frame_ = false;
        continue;
      }

      rx_buf_[rx_pos_++] = data;

      if (data == END_MARKER) {
        process_frame_(rx_buf_, rx_pos_);
        in_frame_ = false;
      }
    }
  }

  void send_on() { send_command_(CMD_ON, sizeof(CMD_ON)); }
  void send_off() { send_command_(CMD_OFF, sizeof(CMD_OFF)); }

 protected:
  void send_command_(const uint8_t *cmd, size_t len) {
    uint8_t packet[MAX_FRAME_SIZE];
    size_t pos = 0;

    packet[pos++] = START_MARKER;
    uint8_t checksum_acc = 0;
    for (size_t i = 0; i < len; i++) {
      packet[pos++] = cmd[i];
      checksum_acc += cmd[i];
    }
    packet[pos++] = to_bcd(checksum_acc % 100);
    packet[pos++] = END_MARKER;

    write_array(packet, pos);
    flush();
  }

  bool verify_checksum_(const uint8_t *frame, size_t len) {
    if (len < 4) return false;
    uint8_t sum = 0;
    for (size_t i = 1; i < len - 2; i++) {
      sum += frame[i];
    }
    return (sum % 100) == from_bcd(frame[len - 2]);
  }

  void process_frame_(const uint8_t *frame, size_t len) {
    if (!verify_checksum_(frame, len)) {
      ESP_LOGW("fchao", "Checksum error on frame of %u bytes", (unsigned) len);
      return;
    }

    const uint8_t *payload = frame + 1;
    size_t payload_len = len - 3;  // minus START, checksum, END

    if (payload_len != EXPECTED_PAYLOAD_SIZE) return;
    if (payload[0] != 0x01) return;

    awaiting_response_ = false;
    missed_responses_ = 0;

    float v_out = from_bcd(payload[3]) * 100.0f + from_bcd(payload[4]);
    float power = from_bcd(payload[5]) * 100.0f + from_bcd(payload[6]);
    float v_in = (from_bcd(payload[7]) * 100.0f + from_bcd(payload[8])) / 10.0f;
    float temp = from_bcd(payload[9]) * 100.0f + from_bcd(payload[10]);
    bool status_on = !(payload[11] & 0x01);
    bool fan_on = (payload[12] & 0x40) != 0;

    if (voltage_out_) voltage_out_->publish_state(v_out);
    if (voltage_in_) voltage_in_->publish_state(v_in);
    if (power_) power_->publish_state(power);
    if (temperature_) temperature_->publish_state(temp);
    if (fan_) fan_->publish_state(fan_on);
    if (status_) status_->publish_state(status_on);
    if (output_switch_) output_switch_->publish_state(status_on);
  }

  void publish_unavailable_() {
    if (voltage_out_) voltage_out_->publish_state(NAN);
    if (voltage_in_) voltage_in_->publish_state(NAN);
    if (power_) power_->publish_state(NAN);
    if (temperature_) temperature_->publish_state(NAN);
    if (fan_) fan_->publish_state(false);
    if (status_) status_->publish_state(false);
    if (output_switch_) output_switch_->publish_state(false);
  }

  sensor::Sensor *voltage_out_{nullptr};
  sensor::Sensor *voltage_in_{nullptr};
  sensor::Sensor *power_{nullptr};
  sensor::Sensor *temperature_{nullptr};
  binary_sensor::BinarySensor *fan_{nullptr};
  binary_sensor::BinarySensor *status_{nullptr};
  switch_::Switch *output_switch_{nullptr};
  switch_::Switch *relay_switch_{nullptr};

  uint8_t rx_buf_[MAX_FRAME_SIZE];
  size_t rx_pos_{0};
  bool in_frame_{false};
  bool relay_was_on_{false};
  bool awaiting_response_{false};
  uint8_t missed_responses_{0};
};

class FchaoInverterSwitch : public switch_::Switch, public Parented<FchaoInverter> {
 protected:
  void write_state(bool state) override {
    if (!this->parent_->is_relay_on()) return;
    if (state) {
      this->parent_->send_on();
    } else {
      this->parent_->send_off();
    }
    // State will be confirmed by the next poll response
  }
};

}  // namespace fchao_inverter
}  // namespace esphome
