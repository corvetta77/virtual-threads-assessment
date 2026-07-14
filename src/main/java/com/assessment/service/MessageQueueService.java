package com.assessment.service;

import com.assessment.model.Message;
import org.springframework.stereotype.Service;

import java.util.concurrent.ConcurrentLinkedQueue;
import java.util.concurrent.atomic.AtomicLong;

@Service
public class MessageQueueService {

    private final ConcurrentLinkedQueue<Message> queue = new ConcurrentLinkedQueue<>();
    private final AtomicLong totalEnqueued = new AtomicLong(0);
    private final AtomicLong totalDequeued = new AtomicLong(0);

    public void enqueue(Message message) {
        queue.offer(message);
        totalEnqueued.incrementAndGet();
    }

    public Message dequeue() {
        Message message = queue.poll();
        if (message != null) {
            totalDequeued.incrementAndGet();
        }
        return message;
    }

    public int size() {
        return queue.size();
    }

    public long getTotalEnqueued() {
        return totalEnqueued.get();
    }

    public long getTotalDequeued() {
        return totalDequeued.get();
    }
}
