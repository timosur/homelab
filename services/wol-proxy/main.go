package main

import (
	"context"
	"flag"
	"fmt"
	"log"
	"net/http"
	"os"
	"os/signal"
	"syscall"
)

func main() {
	configPath := flag.String("config", "/config/config.yaml", "Path to config file")
	flag.Parse()

	log.SetFlags(log.LstdFlags | log.Lshortfile)
	log.Println("Starting WoL Proxy")

	// Load configuration
	config, err := LoadConfig(*configPath)
	if err != nil {
		log.Fatalf("Failed to load config: %v", err)
	}

	if len(config.Backends) == 0 {
		log.Fatal("No backends configured")
	}

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	// Handle shutdown signals
	sigChan := make(chan os.Signal, 1)
	signal.Notify(sigChan, syscall.SIGINT, syscall.SIGTERM)
	go func() {
		<-sigChan
		log.Println("Shutting down...")
		cancel()
	}()

	// Start each backend proxy
	for _, backendConfig := range config.Backends {
		backend := NewProxyBackend(backendConfig)

		// Start idle watcher
		go backend.StartIdleWatcher(ctx)

		// Start HTTP server for this backend
		go func(b *ProxyBackend) {
			addr := fmt.Sprintf(":%d", b.config.ListenPort)
			server := &http.Server{
				Addr:    addr,
				Handler: b.CreateHTTPHandler(),
			}

			log.Printf("[%s] HTTP proxy listening on %s -> %s:%d",
				b.config.Name, addr, b.config.TargetHost, b.config.TargetPort)

			go func() {
				<-ctx.Done()
				server.Shutdown(context.Background())
			}()

			if err := server.ListenAndServe(); err != http.ErrServerClosed {
				log.Printf("[%s] HTTP server error: %v", b.config.Name, err)
			}
		}(backend)
	}

	// Wait for shutdown
	<-ctx.Done()
	log.Println("WoL Proxy stopped")
}
