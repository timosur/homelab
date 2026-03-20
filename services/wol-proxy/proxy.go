package main

import (
	"context"
	"fmt"
	"io"
	"log"
	"net"
	"net/http"
	"net/http/httputil"
	"net/url"
	"os/exec"
	"strings"
	"sync"
	"time"
)

type ProxyBackend struct {
	config       Backend
	lastActivity time.Time
	mu           sync.RWMutex
	isAwake      bool
}

func NewProxyBackend(config Backend) *ProxyBackend {
	return &ProxyBackend{
		config:       config,
		lastActivity: time.Now(),
		isAwake:      false,
	}
}

// SendWoL sends a Wake-on-LAN magic packet
func (p *ProxyBackend) SendWoL() error {
	mac, err := net.ParseMAC(p.config.WoLMAC)
	if err != nil {
		return fmt.Errorf("invalid MAC address: %w", err)
	}

	// Build magic packet: 6 bytes of 0xFF followed by MAC repeated 16 times
	packet := make([]byte, 102)
	for i := 0; i < 6; i++ {
		packet[i] = 0xFF
	}
	for i := 0; i < 16; i++ {
		copy(packet[6+i*6:], mac)
	}

	// Send to broadcast address on UDP port 9
	addr := fmt.Sprintf("%s:9", p.config.WoLBroadcast)
	conn, err := net.Dial("udp", addr)
	if err != nil {
		return fmt.Errorf("failed to connect to broadcast: %w", err)
	}
	defer conn.Close()

	_, err = conn.Write(packet)
	if err != nil {
		return fmt.Errorf("failed to send WoL packet: %w", err)
	}

	log.Printf("[%s] Sent WoL packet to %s", p.config.Name, p.config.WoLMAC)
	return nil
}

// CheckBackendHealth checks if the backend is reachable
func (p *ProxyBackend) CheckBackendHealth() bool {
	addr := fmt.Sprintf("%s:%d", p.config.TargetHost, p.config.TargetPort)
	conn, err := net.DialTimeout("tcp", addr, 2*time.Second)
	if err != nil {
		return false
	}
	conn.Close()
	return true
}

// WakeAndWait sends WoL and waits for backend to become available
func (p *ProxyBackend) WakeAndWait(ctx context.Context) error {
	// First check if already awake
	if p.CheckBackendHealth() {
		p.mu.Lock()
		p.isAwake = true
		p.mu.Unlock()
		return nil
	}

	// Send WoL packet
	if err := p.SendWoL(); err != nil {
		return err
	}

	// Poll until backend is ready or timeout
	timeout := time.Duration(p.config.WakeTimeoutSeconds) * time.Second
	deadline := time.Now().Add(timeout)
	ticker := time.NewTicker(2 * time.Second)
	defer ticker.Stop()

	log.Printf("[%s] Waiting for backend to wake (timeout: %v)", p.config.Name, timeout)

	for {
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-ticker.C:
			if time.Now().After(deadline) {
				return fmt.Errorf("backend did not wake within %v", timeout)
			}
			if p.CheckBackendHealth() {
				log.Printf("[%s] Backend is now awake", p.config.Name)
				p.mu.Lock()
				p.isAwake = true
				p.mu.Unlock()
				// Give it a moment to fully initialize
				time.Sleep(2 * time.Second)
				return nil
			}
		}
	}
}

// SuspendBackend sends SSH command to suspend the backend
func (p *ProxyBackend) SuspendBackend() error {
	if p.config.SSHUser == "" {
		log.Printf("[%s] No SSH user configured, skipping suspend", p.config.Name)
		return nil
	}

	log.Printf("[%s] Suspending backend via SSH", p.config.Name)

	// Use SSH to suspend the machine
	cmd := exec.Command("ssh",
		"-i", p.config.SSHKeyPath,
		"-o", "StrictHostKeyChecking=no",
		"-o", "UserKnownHostsFile=/dev/null",
		"-o", "ConnectTimeout=10",
		fmt.Sprintf("%s@%s", p.config.SSHUser, p.config.TargetHost),
		"sudo", "systemctl", "suspend",
	)

	output, err := cmd.CombinedOutput()
	if err != nil {
		// SSH connection will likely fail when machine suspends, that's expected
		if strings.Contains(string(output), "closed by remote host") ||
			strings.Contains(err.Error(), "exit status 255") {
			log.Printf("[%s] Backend suspended (connection closed as expected)", p.config.Name)
			p.mu.Lock()
			p.isAwake = false
			p.mu.Unlock()
			return nil
		}
		return fmt.Errorf("failed to suspend: %w, output: %s", err, output)
	}

	p.mu.Lock()
	p.isAwake = false
	p.mu.Unlock()
	return nil
}

// UpdateActivity records the current time as last activity
func (p *ProxyBackend) UpdateActivity() {
	p.mu.Lock()
	p.lastActivity = time.Now()
	p.mu.Unlock()
}

// GetIdleTime returns how long since last activity
func (p *ProxyBackend) GetIdleTime() time.Duration {
	p.mu.RLock()
	defer p.mu.RUnlock()
	return time.Since(p.lastActivity)
}

// IsAwake returns whether the backend is known to be awake
func (p *ProxyBackend) IsAwake() bool {
	p.mu.RLock()
	defer p.mu.RUnlock()
	return p.isAwake
}

// StartIdleWatcher monitors for idle timeout and suspends backend
func (p *ProxyBackend) StartIdleWatcher(ctx context.Context) {
	if p.config.IdleTimeoutMinutes <= 0 {
		log.Printf("[%s] Idle timeout disabled", p.config.Name)
		return
	}

	ticker := time.NewTicker(1 * time.Minute)
	defer ticker.Stop()

	idleTimeout := time.Duration(p.config.IdleTimeoutMinutes) * time.Minute

	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			if !p.IsAwake() {
				continue
			}

			idleTime := p.GetIdleTime()
			if idleTime >= idleTimeout {
				log.Printf("[%s] Backend idle for %v, suspending", p.config.Name, idleTime)
				if err := p.SuspendBackend(); err != nil {
					log.Printf("[%s] Failed to suspend: %v", p.config.Name, err)
				}
			}
		}
	}
}

// CreateHTTPHandler creates an HTTP handler that proxies requests
func (p *ProxyBackend) CreateHTTPHandler() http.Handler {
	targetURL := &url.URL{
		Scheme: "http",
		Host:   fmt.Sprintf("%s:%d", p.config.TargetHost, p.config.TargetPort),
	}

	proxy := httputil.NewSingleHostReverseProxy(targetURL)

	// Custom transport with reasonable timeouts
	proxy.Transport = &http.Transport{
		DialContext: (&net.Dialer{
			Timeout:   30 * time.Second,
			KeepAlive: 30 * time.Second,
		}).DialContext,
		MaxIdleConns:          100,
		IdleConnTimeout:       90 * time.Second,
		ResponseHeaderTimeout: 300 * time.Second, // LLM responses can be slow
	}

	// Handle streaming responses properly
	proxy.FlushInterval = 100 * time.Millisecond

	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		// Update activity timestamp
		p.UpdateActivity()

		// Check if backend is awake, wake if needed
		if !p.CheckBackendHealth() {
			log.Printf("[%s] Backend not responding, attempting wake", p.config.Name)

			ctx, cancel := context.WithTimeout(r.Context(), time.Duration(p.config.WakeTimeoutSeconds)*time.Second)
			defer cancel()

			if err := p.WakeAndWait(ctx); err != nil {
				log.Printf("[%s] Failed to wake backend: %v", p.config.Name, err)
				http.Error(w, fmt.Sprintf("Failed to wake GPU node: %v", err), http.StatusServiceUnavailable)
				return
			}
		}

		// Proxy the request
		proxy.ServeHTTP(w, r)
	})
}

// CreateTCPProxy creates a TCP proxy for non-HTTP protocols
func (p *ProxyBackend) CreateTCPProxy(ctx context.Context) error {
	listener, err := net.Listen("tcp", fmt.Sprintf(":%d", p.config.ListenPort))
	if err != nil {
		return fmt.Errorf("failed to listen on port %d: %w", p.config.ListenPort, err)
	}

	go func() {
		<-ctx.Done()
		listener.Close()
	}()

	log.Printf("[%s] TCP proxy listening on :%d -> %s:%d",
		p.config.Name, p.config.ListenPort, p.config.TargetHost, p.config.TargetPort)

	for {
		conn, err := listener.Accept()
		if err != nil {
			select {
			case <-ctx.Done():
				return nil
			default:
				log.Printf("[%s] Accept error: %v", p.config.Name, err)
				continue
			}
		}

		go p.handleTCPConnection(ctx, conn)
	}
}

func (p *ProxyBackend) handleTCPConnection(ctx context.Context, clientConn net.Conn) {
	defer clientConn.Close()

	p.UpdateActivity()

	// Check if backend is awake
	if !p.CheckBackendHealth() {
		log.Printf("[%s] Backend not responding, attempting wake", p.config.Name)

		wakeCtx, cancel := context.WithTimeout(ctx, time.Duration(p.config.WakeTimeoutSeconds)*time.Second)
		defer cancel()

		if err := p.WakeAndWait(wakeCtx); err != nil {
			log.Printf("[%s] Failed to wake backend: %v", p.config.Name, err)
			return
		}
	}

	// Connect to backend
	targetAddr := fmt.Sprintf("%s:%d", p.config.TargetHost, p.config.TargetPort)
	backendConn, err := net.DialTimeout("tcp", targetAddr, 10*time.Second)
	if err != nil {
		log.Printf("[%s] Failed to connect to backend: %v", p.config.Name, err)
		return
	}
	defer backendConn.Close()

	// Bidirectional copy
	done := make(chan struct{})

	go func() {
		io.Copy(backendConn, clientConn)
		done <- struct{}{}
	}()

	go func() {
		io.Copy(clientConn, backendConn)
		done <- struct{}{}
	}()

	// Wait for one direction to finish
	<-done

	// Update activity when connection closes
	p.UpdateActivity()
}
