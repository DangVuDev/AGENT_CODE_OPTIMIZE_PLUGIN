package config

import (
	"os"
	"time"
)

type Config struct {
	Address                                    string
	ReadTimeout, WriteTimeout, ShutdownTimeout time.Duration
}

func Load() Config {
	address := os.Getenv("HTTP_ADDRESS")
	if address == "" {
		address = ":8080"
	}
	return Config{Address: address, ReadTimeout: 5 * time.Second, WriteTimeout: 10 * time.Second, ShutdownTimeout: 10 * time.Second}
}
