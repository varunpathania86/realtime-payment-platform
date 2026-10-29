package main

import (
	"context"
	"net"
	"net/http"
	"testing"
	"time"
)

func TestServerBounds(t *testing.T) {
	s := newServer(":8080", http.NotFoundHandler())
	if s.ReadHeaderTimeout <= 0 || s.ReadTimeout <= 0 || s.WriteTimeout <= 0 || s.IdleTimeout <= 0 || s.MaxHeaderBytes <= 0 {
		t.Fatal("all server limits must be bounded")
	}
}

func TestGracefulShutdown(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	server := newServer("127.0.0.1:0", http.NotFoundHandler())
	listening := make(chan struct{})
	server.BaseContext = func(net.Listener) context.Context {
		close(listening)
		return ctx
	}
	done := make(chan error, 1)
	go func() { done <- run(ctx, server) }()
	select {
	case <-listening:
	case <-time.After(5 * time.Second):
		t.Fatal("server did not start")
	}
	cancel()
	select {
	case err := <-done:
		if err != nil {
			t.Fatal(err)
		}
	case <-time.After(5 * time.Second):
		t.Fatal("shutdown timed out")
	}
}
