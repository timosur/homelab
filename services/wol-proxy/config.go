package main

import (
	"fmt"
	"os"

	"gopkg.in/yaml.v3"
)

type Config struct {
	Backends []Backend `yaml:"backends"`
}

type Backend struct {
	Name               string `yaml:"name"`
	ListenPort         int    `yaml:"listenPort"`
	TargetHost         string `yaml:"targetHost"`
	TargetPort         int    `yaml:"targetPort"`
	WoLMAC             string `yaml:"wolMac"`
	WoLBroadcast       string `yaml:"wolBroadcast"`
	IdleTimeoutMinutes int    `yaml:"idleTimeoutMinutes"`
	WakeTimeoutSeconds int    `yaml:"wakeTimeoutSeconds"`
	SSHUser            string `yaml:"sshUser"`
	SSHKeyPath         string `yaml:"sshKeyPath"`
}

func LoadConfig(path string) (*Config, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("failed to read config file: %w", err)
	}

	var config Config
	if err := yaml.Unmarshal(data, &config); err != nil {
		return nil, fmt.Errorf("failed to parse config: %w", err)
	}

	// Set defaults
	for i := range config.Backends {
		if config.Backends[i].WakeTimeoutSeconds == 0 {
			config.Backends[i].WakeTimeoutSeconds = 120
		}
		if config.Backends[i].IdleTimeoutMinutes == 0 {
			config.Backends[i].IdleTimeoutMinutes = 30
		}
		if config.Backends[i].WoLBroadcast == "" {
			config.Backends[i].WoLBroadcast = "255.255.255.255"
		}
		if config.Backends[i].SSHKeyPath == "" {
			config.Backends[i].SSHKeyPath = "/secrets/ssh-key"
		}
	}

	return &config, nil
}
