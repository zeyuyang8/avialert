import torch
from .data.coco.coco_utils import get_coco_api_from_dataset
from .data.coco.coco_eval import CocoEvaluator
import pandas as pd


def get_od_predictions(model, dataloader, device, idx):
    '''
    Returns object detection predictions for a given index in a dataloader.

    Args:
        model (Torch object): Object detection model
        dataloader: DataLoader
        device (str): Device to run training on ('cpu' or 'cuda')
        idx (int): Index of the image to predict on

    Returns:
        object detection results for a given index in a dataloader
    '''
    model.eval()
    with torch.no_grad():
        for batch_id, (images, targets) in enumerate(dataloader):
            if batch_id == idx:
                # move data to device
                images = list(image.to(device) for image in images)
                targets = [{k: v.to(device) for k, v in t.items()} for t in targets]

                # forward pass
                outputs = model(images)
                return outputs
    return None


def get_od_loss(model, loss_fn, dataloader, device):
    '''
    Returns loss for an object detection model on a given dataset.

    Args:
        model (Torch object): Torch object detection model
        loss_fn (function): Loss function TODO: customize and use loss_fn in the future
        dataloader: DataLoader
        device (str): Device to run training on ('cpu' or 'cuda')

    Returns:
        Loss for an object detection model on a given dataset.
    '''
    loss = 0
    with torch.no_grad():
        for batch_id, (images, targets) in enumerate(dataloader):
            # move data to device
            images = list(image.to(device) for image in images)
            targets = [{k: v.to(device) for k, v in t.items()} for t in targets]

            # forward pass
            loss_dict = model(images, targets)
            losses = sum(loss for loss in loss_dict.values())
            loss += losses.item()
    loss = loss / len(dataloader)
    return loss


def get_od_stats(model, dataloader, device):
    '''
    Returns statistics for an object detection model on a given dataset.

    Args:
        model (Torch model): Object detection model
        dataloader: DataLoader
        device (stR): Device to use

    Returns:
        Statistics for an object detection model on a given dataset.
    '''
    n_threads = torch.get_num_threads()
    # print(f"Using {n_threads} threads for inference")
    torch.set_num_threads(n_threads)

    cpu_device = torch.device("cpu")
    model.eval()
    coco = get_coco_api_from_dataset(dataloader.dataset)
    iou_types = ["bbox"]
    coco_evaluator = CocoEvaluator(coco, iou_types)

    with torch.no_grad():
        for batch, (images, targets) in enumerate(dataloader):
            images = list(img.to(device) for img in images)
            if torch.cuda.is_available():
                torch.cuda.synchronize()
            outputs = model(images)
            outputs = [{key: val.to(cpu_device) for key, val in out.items()} for out in outputs]
            res = {target["image_id"].item(): output for target, output in zip(targets, outputs)}
            coco_evaluator.update(res)

    # gather the stats from all processes
    coco_evaluator.synchronize_between_processes()

    coco_evaluator.accumulate()
    coco_evaluator.summarize()

    stats = coco_evaluator.coco_eval['bbox'].stats

    return stats


def get_clf_loss_accuracy(model, loss_fn, dataloader, device):
    '''
    Returns loss and accuracy for a classifier model on a given dataset.

    Args:
        model (Torch model): Classifier model
        loss_fn (function): Loss function
        dataloader: DataLoader
        device (sr): Device to run training on ('cpu' or 'cuda')

    Returns:
        Loss and accuracy for a classifier model on a given dataset.
    '''
    # Set model to evaluation mode
    model.eval()

    # Initialize variables
    correct = 0
    cumulative_loss = 0
    n_samples = 0

    with torch.no_grad():
        for batch_id, (inputs, labels) in enumerate(dataloader):
            inputs, labels = inputs.to(device), labels.to(device)
            # Loss
            predicted = model(inputs)
            loss = loss_fn(predicted, labels)
            cumulative_loss += loss.item()

            # Accuracy
            predicted_labels = predicted.detach().softmax(dim=1)
            dummy_max_vals, max_ids = predicted_labels.max(dim=1)
            correct += (max_ids == labels).sum().cpu().item()
            n_samples += inputs.size(0)

    loss = cumulative_loss / len(dataloader)
    accuracy = correct / n_samples
    return loss, accuracy


def get_clf_predictions(model, dataloader, device):
    '''
    Returns the true labels and predicted labels of the given dataset.

    Args:
        model (Torch object): Classifier model
        dataloader: DataLoader
        device (str): Device to run training on ('cpu' or 'cuda')

    Returns:
        A tuple of two lists, containing the true labels and predicted labels respectively.
    '''
    model.eval()
    true_labels = []
    out_labels = []
    with torch.no_grad():
        for batch_id, (inputs, labels) in enumerate(dataloader):
            true_labels.append(labels)
            inputs, labels = inputs.to(device), labels.to(device)
            # Accuracy
            predicted = model(inputs)
            predicted_labels = predicted.detach().softmax(dim=1)
            dummy_max_vals, max_ids = predicted_labels.max(dim=1)
            out_labels.append(max_ids)
    return true_labels, out_labels


def get_stats_from_confusion_matrix(confusion_matrix, class_names):
    '''
    Returns precision, recall, f1-score, and support for each class given a confusion matrix.

    Args:
        confusion_matrix (numpy array): Confusion matrix object
        class_names (list of str): List of class names

    Returns:
        A dictionary with precision, recall, f1-score, and support for each class.
    '''
    recall = confusion_matrix.diagonal() / confusion_matrix.sum(axis=0)
    precision = confusion_matrix.diagonal() / confusion_matrix.sum(axis=1)
    f1_score = 2 * precision * recall / (precision + recall)
    stats = pd.DataFrame({'class': class_names, 'precision': precision, 'recall': recall, 'f1_score': f1_score})
    return stats
