package com.assessment.service;

import com.assessment.model.Message;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Service;

import java.util.concurrent.atomic.AtomicInteger;

/**
 * Produces 100 messages/second into the in-memory queue.
 * Scheduled every 100ms, producing 10 messages per tick.
 */
@Service
public class MessageProducerService {

    private final MessageQueueService messageQueueService;
    private final AtomicInteger counter = new AtomicInteger(0);

    public MessageProducerService(MessageQueueService messageQueueService) {
        this.messageQueueService = messageQueueService;
    }

    @Scheduled(fixedRate = 50)
    public void produce() {
        for (int i = 0; i < 20; i++) {
            int seq = counter.incrementAndGet();
            messageQueueService.enqueue(Message.create("payload-" + seq));
        }
    }
}
